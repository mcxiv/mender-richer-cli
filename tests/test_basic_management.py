"""Regression tests for Basic-plan device, group, and deployment management."""

import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from api import DEPLOYMENTS, DEPLOYMENT_LIST, GROUPS, INVENTORY_DEVICE, MenderAPI
import display
import main


def response(value=None, *, status=200, headers=None, text=None):
    """Build an HTTP response without contacting a live tenant."""
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers or {})
    result._content = (text.encode("utf-8") if text is not None else
                       json.dumps(value).encode("utf-8") if value is not None else b"")
    result._content_consumed = True
    result.encoding = "utf-8"
    return result


def selected(index):
    """Return the picker result for an original row index, or cancellation."""
    return ([] if index is None else [index], -1, None, False)


class APITests(unittest.TestCase):
    def setUp(self):
        settings = SimpleNamespace(server="https://example.com", token="test", skip_verify=False, verbose=False)
        self.client = MenderAPI(settings)
        self.request = Mock()
        self.client.session.request = self.request

    def test_rename_uses_additive_name_tag_without_overwriting_other_tags(self):
        self.request.return_value = response()
        self.client.rename_device("device/id", "  Test bench  ")
        self.assertEqual(self.request.call_args.args, ("PATCH", "https://example.com" + INVENTORY_DEVICE + "/device%2Fid/tags"))
        self.assertEqual(self.request.call_args.kwargs["json"], [{"name": "name", "value": "Test bench"}])

    def test_empty_names_and_tag_sets_do_not_send_requests(self):
        for operation in [lambda: self.client.rename_device("id", " "),
                          lambda: self.client.set_tags("id", {}),
                          lambda: self.client.set_tags("id", {" ": "value"})]:
            with self.assertRaises(ValueError):
                operation()
        self.request.assert_not_called()

    def test_tag_removal_preserves_unrelated_values_and_descriptions(self):
        inventory = {"attributes": [
            {"scope": "inventory", "name": "hostname", "value": "device"},
            {"scope": "tags", "name": "name", "value": "bench", "description": "Friendly name"},
            {"scope": "tags", "name": "site", "value": "Paris", "timestamp": "yesterday"},
            {"scope": "tags", "name": "obsolete", "value": "yes"},
        ]}
        self.request.side_effect = [response(inventory, headers={"ETag": '"revision-1"'}), response()]
        self.client.remove_tag("id", "obsolete")
        call = self.request.call_args
        self.assertEqual(call.args[0], "PUT")
        self.assertEqual(call.kwargs["headers"], {"If-Match": '"revision-1"'})
        self.assertEqual(call.kwargs["json"], [
            {"name": "name", "value": "bench", "description": "Friendly name"},
            {"name": "site", "value": "Paris"},
        ])

    def test_tag_conflict_does_not_retry_or_overwrite_newer_tags(self):
        inventory = {"attributes": [{"scope": "tags", "name": "site", "value": "Paris"}]}
        self.request.side_effect = [response(inventory, headers={"ETag": "revision-1"}), response(status=412)]
        with self.assertRaisesRegex(RuntimeError, "changed during the edit"):
            self.client.remove_tag("id", "site")
        self.assertEqual(self.request.call_count, 2)

    def test_missing_tag_or_etag_does_not_replace_tags(self):
        inventory = {"attributes": [{"scope": "tags", "name": "site", "value": "Paris"}]}
        self.request.return_value = response(inventory)
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.client.remove_tag("id", "missing")
        with self.assertRaisesRegex(RuntimeError, "ETag"):
            self.client.remove_tag("id", "site")
        self.assertEqual([call.args[0] for call in self.request.call_args_list], ["GET", "GET"])

    def test_groups_are_strings_and_sorted_without_extra_queries(self):
        self.request.return_value = response(["zebra", "Alpha", "beta"])
        self.assertEqual(self.client.groups(), ["Alpha", "beta", "zebra"])
        self.request.assert_called_once()

    def test_group_ids_are_collected_across_pages(self):
        ids = [f"id-{i}" for i in range(100)]
        self.request.side_effect = [response(ids, headers={"X-Total-Count": "101"}), response(["last"])]
        self.assertEqual(self.client.group_devices("test-group"), ids + ["last"])
        self.assertEqual(self.request.call_args.args[1], "https://example.com" + GROUPS + "/test-group/devices")
        self.assertEqual(self.request.call_args.kwargs["params"], {"page": 2, "per_page": 100})

    def test_group_membership_operations_use_documented_bodies(self):
        self.request.side_effect = [response({"matched_count": 2, "updated_count": 1}),
                                    response({"updated_count": 1}), response(status=204)]
        self.assertEqual(self.client.add_group_devices("staging", ["one", "two"])["matched_count"], 2)
        self.assertEqual(self.client.remove_group_devices("staging", ["one"])["updated_count"], 1)
        self.client.assign_group("two", "new-group")
        calls = self.request.call_args_list
        self.assertEqual([c.args[0] for c in calls], ["PATCH", "DELETE", "PUT"])
        self.assertEqual(calls[0].kwargs["json"], ["one", "two"])
        self.assertEqual(calls[1].kwargs["json"], ["one"])
        self.assertEqual(calls[2].kwargs["json"], {"group": "new-group"})

    def test_invalid_groups_and_empty_membership_do_not_send_requests(self):
        for name in ["", "two words", "bad/name", "x" * 257]:
            with self.assertRaises(ValueError):
                self.client.group_devices(name)
        for operation in [self.client.add_group_devices, self.client.remove_group_devices]:
            with self.assertRaises(ValueError):
                operation("test", [])
        self.request.assert_not_called()

    def test_device_without_a_group_returns_none(self):
        self.request.return_value = response({"group": None})
        self.assertIsNone(self.client.device_group("id"))

    def test_device_history_respects_twenty_row_page_limit(self):
        entries = [{"deployment": {"id": str(i)}, "device": {"status": "success"}} for i in range(20)]
        self.request.side_effect = [response(entries), response([])]
        self.assertEqual(self.client.device_deployments("id"), entries)
        self.assertEqual([c.kwargs["params"] for c in self.request.call_args_list],
                         [{"page": 1, "per_page": 20}, {"page": 2, "per_page": 20}])

    def test_device_result_filter_is_preserved_across_pages(self):
        rows = [{"id": str(i), "status": "failure", "log": True} for i in range(100)]
        self.request.side_effect = [response(rows), response([])]
        self.assertEqual(self.client.deployment_devices("deployment/id", "failure"), rows)
        for call in self.request.call_args_list:
            self.assertIn("/deployment%2Fid/devices/list", call.args[1])
            self.assertEqual(call.kwargs["params"]["status"], "failure")

    def test_log_is_plain_text_and_response_is_closed(self):
        log = response(text="Failed: bad signature\r\nDetails: é\n")
        log.close = Mock()
        self.request.return_value = log
        self.assertEqual(self.client.deployment_log("deployment/id", "device/id"), log.text)
        self.assertIn("/deployment%2Fid/devices/device%2Fid/log", self.request.call_args.args[1])
        log.close.assert_called_once()

    def test_unavailable_log_has_actionable_error_and_closes_response(self):
        log = response(status=404)
        log.close = Mock()
        self.request.return_value = log
        with self.assertRaisesRegex(RuntimeError, "may not have uploaded"):
            self.client.deployment_log("deployment", "device")
        log.close.assert_called_once()

    def test_deployment_listing_uses_v2_name_filter(self):
        self.request.return_value = response([], headers={"X-Total-Count": "0"})
        self.assertEqual(self.client.deployments(1, 20, "finished", "release"), ([], 0))
        self.assertEqual(self.request.call_args.args[1], "https://example.com" + DEPLOYMENT_LIST)
        self.assertEqual(self.request.call_args.kwargs["params"],
                         {"page": 1, "per_page": 20, "status": "finished", "name": "release"})

    def test_existing_scheduled_listing_retains_legacy_endpoint(self):
        self.request.return_value = response([])
        self.client.deployments(1, 20, "scheduled", "release")
        self.assertEqual(self.request.call_args.args[1], "https://example.com" + DEPLOYMENTS)
        self.assertEqual(self.request.call_args.kwargs["params"]["search"], "release")


class CLITests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.runner = CliRunner()
        settings = SimpleNamespace(server="https://example.com", token="test")
        self.config_patch = patch.object(main.Config, "resolve", return_value=settings)
        self.api_patch = patch.object(main, "MenderAPI", return_value=self.client)
        self.config_patch.start()
        self.api_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.addCleanup(self.api_patch.stop)

    def invoke(self, args, **kwargs):
        return self.runner.invoke(main.cli, args, **kwargs)

    def test_positional_credentials_accept_new_group_command(self):
        self.client.groups.return_value = ["staging"]
        result = self.invoke(["example.com", "test-token", "groups", "list", "--raw"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output), ["staging"])

    def test_rename_and_additive_tag_assignments(self):
        result = self.invoke(["devices", "rename", "id", "New name"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.client.rename_device.assert_called_once_with("id", "New name")
        result = self.invoke(["devices", "tags", "set", "id", "site=Paris", "query=a=b", "empty="])
        self.assertEqual(result.exit_code, 0, result.output)
        self.client.set_tags.assert_called_once_with("id", {"site": "Paris", "query": "a=b", "empty": ""})

    def test_bad_tag_syntax_does_not_mutate_device(self):
        result = self.invoke(["devices", "tags", "set", "id", "missing-equals"])
        self.assertEqual(result.exit_code, 2, result.output)
        self.client.set_tags.assert_not_called()

    def test_cancelled_tag_and_group_changes_do_not_mutate(self):
        for args in [["devices", "tags", "remove", "id", "site"],
                     ["groups", "add", "staging", "id"], ["groups", "remove", "staging", "id"]]:
            with self.subTest(args=args):
                with patch.object(main, "confirm", return_value=False):
                    self.assertEqual(self.invoke(args).exit_code, 0)
        self.client.remove_tag.assert_not_called()
        self.client.add_group_devices.assert_not_called()
        self.client.remove_group_devices.assert_not_called()

    def test_group_add_reports_partial_match_as_failure(self):
        self.client.add_group_devices.return_value = {"matched_count": 1, "updated_count": 1}
        result = self.invoke(["groups", "add", "staging", "one", "one", "missing", "--yes"])
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("matched 1 of 2", result.output)
        self.client.add_group_devices.assert_called_once_with("staging", ["one", "missing"])

    def test_group_deployment_uses_only_snapshot_ids_and_existing_basic_api(self):
        self.client.group_devices.return_value = ["one", "two"]
        result = self.invoke(["groups", "deploy", "staging", "release", "--name", "test run", "--yes"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.client.deploy.assert_called_once_with("release", ["one", "two"], "test run")

    def test_empty_or_cancelled_group_deployment_does_not_deploy(self):
        self.client.group_devices.return_value = []
        result = self.invoke(["groups", "deploy", "empty", "release", "--yes"])
        self.assertEqual(result.exit_code, 1)
        self.client.group_devices.return_value = ["one"]
        with patch.object(main, "confirm", return_value=False):
            result = self.invoke(["groups", "deploy", "staging", "release"])
        self.assertEqual(result.exit_code, 0)
        self.client.deploy.assert_not_called()

    def test_raw_device_results_and_history(self):
        rows = [{"id": "one", "status": "failure", "log": True}]
        self.client.deployment_devices.return_value = rows
        result = self.invoke(["deployments", "devices", "deployment", "--status", "failure", "--raw"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output), rows)
        self.client.deployment_devices.assert_called_once_with("deployment", "failure")
        history = [{"deployment": {"id": "deployment", "name": "test"}, "device": rows[0]}]
        self.client.device_deployments.return_value = history
        result = self.invoke(["devices", "deployments", "one", "--raw"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output), history)

    def test_log_printing_and_file_output(self):
        self.client.deployment_log.return_value = "Failure: é\r\nMore details\n"
        result = self.invoke(["deployments", "logs", "deployment", "one"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.output, "Failure: é\nMore details\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "log.txt"
            result = self.invoke(["deployments", "logs", "deployment", "one", "--output", str(path)])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(path.read_bytes(), "Failure: é\r\nMore details\n".encode())

    def test_missing_log_is_reported_without_traceback(self):
        self.client.deployment_log.side_effect = RuntimeError("no deployment log is available")
        result = self.invoke(["deployments", "logs", "deployment", "one"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("no deployment log", result.output)
        self.assertNotIn("Traceback", result.output)

    def test_all_nested_help_hides_sphinx_parameter_fields(self):
        def visit(command, prefix):
            result = self.invoke([*prefix, "--help"])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertNotIn(":param", result.output)
            for name, child in getattr(command, "commands", {}).items():
                visit(child, [*prefix, name])
        visit(main.cli, [])


class TUITests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.app = SimpleNamespace(api=self.client, config=SimpleNamespace(server="https://example.com"))

    def test_main_menu_reaches_groups_and_deployments(self):
        with patch.object(main, "pick", side_effect=[selected(1), selected(2), selected(None)]), \
             patch.object(main, "_interactive_groups") as groups, \
             patch.object(main, "_interactive_deployments") as deployments, patch("builtins.print"):
            main.interactive(self.app)
        groups.assert_called_once_with(self.app)
        deployments.assert_called_once_with(self.app)

    def test_rename_refreshes_the_device_menu_title(self):
        device = {"id": "one", "attributes": [{"scope": "tags", "name": "name", "value": "old"}]}
        with patch.object(main, "pick", side_effect=[selected(6), selected(None)]) as picker, \
             patch.object(main.click, "prompt", return_value="new name"):
            main._device_menu(self.app, device, [device])
        self.client.rename_device.assert_called_once_with("one", "new name")
        self.assertTrue(picker.call_args.args[0].startswith("new name"))

    def test_tag_editor_preserves_equals_signs_in_values(self):
        self.client.tags.return_value = []
        with patch.object(main, "pick", side_effect=[selected(0), selected(None)]), \
             patch.object(main.click, "prompt", side_effect=["site", "a=b"]):
            main._interactive_tags(self.app, "one")
        self.client.set_tags.assert_called_once_with("one", {"site": "a=b"})

    def test_deployment_results_open_log_for_selected_device(self):
        self.client.deployment_devices.return_value = [{"id": "one", "status": "failure", "log": True}]
        self.client.deployment_log.return_value = "signature mismatch"
        with patch.object(main, "pick", side_effect=[selected(1), selected(0), selected(None)]), \
             patch.object(main, "show_text") as viewer:
            main._interactive_deployment_devices(self.app, "deployment")
        self.client.deployment_devices.assert_called_with("deployment", "failure")
        self.client.deployment_log.assert_called_once_with("deployment", "one")
        viewer.assert_called_once_with("Log for one", "signature mismatch")

    def test_no_log_flag_does_not_trigger_a_log_request(self):
        self.client.deployment_devices.return_value = [{"id": "one", "status": "pending", "log": False}]
        with patch.object(main, "pick", side_effect=[selected(0), selected(0), selected(None)]), \
             patch.object(main, "notify") as viewer:
            main._interactive_deployment_devices(self.app, "deployment")
        self.client.deployment_log.assert_not_called()
        self.assertIn("has not uploaded", viewer.call_args.args[1])

    def test_group_deployment_uses_the_full_current_membership(self):
        self.client.groups.return_value = ["staging"]
        self.client.group_devices.return_value = ["one", "two"]
        with patch.object(main, "pick", side_effect=[selected(1), selected(3), selected(None)]), \
             patch.object(main, "_interactive_deploy") as deploy:
            main._interactive_groups(self.app)
        deploy.assert_called_once_with(self.app, ["one", "two"])

    def test_cancelled_group_move_does_not_change_membership(self):
        self.client.device_group.return_value = "old"
        self.client.groups.return_value = ["old", "new"]
        with patch.object(main, "pick", return_value=selected(3)), patch.object(main, "confirm", return_value=False):
            main._interactive_change_group(self.app, "one")
        self.client.assign_group.assert_not_called()

    def test_text_viewer_preserves_full_lines_for_copying(self):
        with patch.object(display.sys.stdin, "isatty", return_value=True), \
             patch.object(display.sys.stdout, "isatty", return_value=True), patch.object(display, "pick") as picker:
            display.show_text("Log", "\tfirst\n" + "x" * 1000)
        self.assertEqual(picker.call_args.args[2], [["1", "    first"], ["2", "x" * 1000]])
        self.assertTrue(picker.call_args.kwargs["viewer"])


if __name__ == "__main__":
    unittest.main()
