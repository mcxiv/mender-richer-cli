"""Command parsing and behavior across authentication, device, artifact, and remote workflows."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from click.testing import CliRunner
import click
import pytest

import main


def run(args):
    return CliRunner().invoke(main.cli,args)


@pytest.mark.parametrize('args', [['server'],['server','--help']])
def test_missing_positional_token_has_usage_error(args):
    value=run(args)
    assert value.exit_code==2 and 'provide both SERVER and TOKEN' in value.output


def test_configuration_and_missing_token_errors(monkeypatch):
    monkeypatch.setattr(main.Config,'resolve',Mock(side_effect=ValueError('bad config')))
    assert 'bad config' in run(['devices','list']).output
    monkeypatch.setattr(main.Config,'resolve',Mock(return_value=SimpleNamespace(token=None)))
    monkeypatch.setattr(main,'MenderAPI',Mock())
    assert 'no token is available' in run(['devices','list']).output


def test_default_picker_interactive_command_and_favorite(cli_app,monkeypatch):
    interactive=Mock()
    monkeypatch.setattr(main,'interactive',interactive)
    assert run([]).exit_code==0
    assert run(['interactive']).exit_code==0
    assert interactive.call_count==2
    favorites=Mock()
    favorites.get.return_value={'id':'favorite'}
    monkeypatch.setattr(main,'Favorites',Mock(return_value=favorites))
    terminal=Mock()
    monkeypatch.setattr(main.remote,'terminal',terminal)
    assert run(['--favorite']).exit_code==0
    terminal.assert_called_once_with(cli_app.api,'favorite')
    favorites.get.return_value=None
    assert 'no favorite device' in run(['--favorite']).output


@pytest.mark.parametrize('prompted',[False,True])
def test_login_with_prompt_or_options(cli_app,monkeypatch,prompted):
    cli_app.config.save_token=Mock()
    cli_app.api.login.return_value=b'token'
    monkeypatch.setattr(main.click,'prompt',Mock(side_effect=['user','password']))
    args=['login'] if prompted else ['login','--username','user','--password','password','--2fa-code','123']
    value=run(args)
    assert value.exit_code==0,value.output
    cli_app.api.login.assert_called_once_with('user','password',None if prompted else '123')
    cli_app.config.save_token.assert_called_once_with(b'token')


@pytest.mark.parametrize('raw',[False,True])
def test_devices_list_and_inventory(cli_app,raw):
    cli_app.api.list_page.return_value=([{'id':'one','status':'accepted','identity_data':{'mac':'00'}}],1)
    cli_app.api.inventory.return_value={'id':'one','nested':{'key':'value'}}
    flags=['--raw'] if raw else []
    value=run(['devices','list',*flags])
    assert value.exit_code==0 and 'one' in value.output
    value=run(['devices','inventory','one',*flags])
    assert value.exit_code==0 and 'value' in value.output


def test_inventory_interactive_view(cli_app,monkeypatch):
    cli_app.api.inventory.return_value={'id':'one'}
    picker=Mock()
    monkeypatch.setattr(main,'pick',picker)
    monkeypatch.setattr(main.sys.stdin,'isatty',lambda:True)
    monkeypatch.setattr(main.sys.stdout,'isatty',lambda:True)
    main.inventory_command.callback.__wrapped__(cli_app,'one',False)
    assert picker.call_args.kwargs['viewer']


@pytest.mark.parametrize('raw',[False,True])
@pytest.mark.parametrize('sort',['name','type'])
def test_artifact_list_sorting(cli_app,raw,sort):
    cli_app.api.list_page.return_value=([{'name':'z','type_info':{'type':'a'}},{'name':'a','type_info':{'type':'z'}}],2)
    value=run(['artifacts','list','--sort',sort,*(['--raw'] if raw else [])])
    assert value.exit_code==0,value.output
    if raw:
        assert [r['name'] for r in json.loads(value.output)]==['z','a']


def test_artifact_upload_and_download(cli_app,tmp_path):
    path=tmp_path/'artifact.mender'
    path.write_bytes(b'fixture')
    value=run(['artifacts','upload',str(path),'--description','test','--no-progress'])
    assert value.exit_code==0,value.output
    cli_app.api.upload_artifact.assert_called_once_with(path,'test',False)
    cli_app.api.download_artifact.return_value=path
    value=run(['artifacts','download','artifact-id','--destination-path',str(path),'--no-progress'])
    assert value.exit_code==0,value.output
    cli_app.api.download_artifact.assert_called_once_with('artifact-id',path,False)


@pytest.mark.parametrize('confirmed',[True,False])
def test_artifact_deletion_and_deployment(cli_app,monkeypatch,confirmed):
    monkeypatch.setattr(main,'confirm',lambda *_:confirmed)
    assert run(['artifacts','delete','artifact']).exit_code==0
    assert run(['artifacts','deploy','release','one']).exit_code==0
    assert run(['artifacts','deploy','release','one','two','--name','test']).exit_code==0
    assert cli_app.api.delete_artifact.called is confirmed
    assert cli_app.api.deploy.call_count==(2 if confirmed else 0)


@pytest.mark.parametrize('raw',[False,True])
def test_deployment_listing_summary_and_results(cli_app,raw):
    cli_app.api.deployments.return_value=([{'id':'d','name':'test','status':'finished'}],1)
    cli_app.api.deployment_details.return_value=({'id':'d','name':'test','device_count':1},{'success':1})
    cli_app.api.deployment_devices.return_value=[{'id':'one','status':'success','log':False}]
    flags=['--raw'] if raw else []
    for args in [['deployments','list'],['deployments','show','d'],['deployments','devices','d']]:
        result=run([*args,*flags])
        assert result.exit_code==0,result.output


@pytest.mark.parametrize('finished',[True,False])
def test_watch_completion_and_interrupt(cli_app,monkeypatch,finished):
    cli_app.api.deployment_details.return_value=({'status':'finished' if finished else 'inprogress'}, {})
    monkeypatch.setattr(main.time,'sleep',Mock(side_effect=KeyboardInterrupt()))
    value=run(['deployments','watch','d','--interval','1'])
    assert value.exit_code==0,value.output
    assert ('Deployment finished' if finished else 'Watch stopped') in value.output


@pytest.mark.parametrize('finished',[True,False])
@pytest.mark.parametrize('confirmed',[True,False])
def test_cancel_deployment(cli_app,monkeypatch,finished,confirmed):
    cli_app.api.deployment_details.return_value=({'status':'finished' if finished else 'inprogress','name':'test'}, {})
    monkeypatch.setattr(main,'confirm',lambda *_:confirmed)
    result=run(['deployments','cancel','d'])
    assert result.exit_code==0,result.output
    assert cli_app.api.cancel_deployment.called is (confirmed and not finished)


def test_remote_terminal_and_port_forward_dispatch(cli_app,monkeypatch):
    terminal=Mock()
    forward=Mock()
    monkeypatch.setattr(main.remote,'terminal',terminal)
    monkeypatch.setattr(main.remote,'port_forward',forward)
    assert run(['terminal','device']).exit_code==0
    assert run(['port-forward','device','8080:80']).exit_code==0
    terminal.assert_called_once_with(cli_app.api,'device')
    forward.assert_called_once_with(cli_app.api,'device','127.0.0.1',['8080:80'])


@pytest.mark.parametrize(('source','destination','valid'), [('file','device:/tmp/file',True),
    ('device:/tmp/file','file',True),('one:/tmp/file','two:/tmp/file',False),('file','other',False)])
def test_copy_endpoints(cli_app,monkeypatch,source,destination,valid):
    monkeypatch.setattr(main,'confirm',lambda *_:True)
    value=run(['cp',source,destination])
    assert (value.exit_code==0) is valid


@pytest.mark.parametrize('value',[r'C:\data\file','C:/file','C:relative',r'\\server\share\file','/tmp/file:name','./file','id:',':path'])
def test_local_path_detection(value):
    assert main._parse_remote(value) is None


@pytest.mark.parametrize('shell',['bash','zsh','fish','elvish'])
def test_completion_scripts(cli_app,shell):
    value=run(['completion',shell])
    assert value.exit_code==0,value.output
    assert value.output


def test_unsupported_completion_is_actionable(cli_app,monkeypatch):
    import click.shell_completion
    monkeypatch.setattr(click.shell_completion,'get_completion_class',lambda _:None)
    value=run(['completion','powershell'])
    assert value.exit_code==1
    assert 'not available' in value.output


def test_main_entrypoint_converts_expected_errors(monkeypatch,capsys):
    for error in [click.ClickException('click error'),RuntimeError('network error')]:
        monkeypatch.setattr(main,'cli',Mock(side_effect=error))
        with pytest.raises(SystemExit) as result:
            main.main()
        assert result.value.code==1
    assert 'network error' in capsys.readouterr().err


def test_require_ids():
    assert main._require_ids(('one','two'))==['one','two']
    with pytest.raises(click.ClickException):
        main._require_ids(())


def test_log_output_write_error(cli_app,monkeypatch):
    cli_app.api.deployment_log.return_value='log'
    monkeypatch.setattr(Path,'open',Mock(side_effect=OSError('disk failed')))
    value=run(['deployments','logs','d','one','--output','file'])
    assert value.exit_code==1 and 'disk failed' in value.output


@pytest.mark.parametrize('raw',[False,True])
def test_metadata_query_commands(cli_app,raw):
    cli_app.api.tags.return_value=[{'name':'site','value':'Paris'}]
    cli_app.api.groups.return_value=['staging']
    cli_app.api.group_devices.return_value=['one']
    cli_app.api.device_deployments.return_value=[{'deployment':{'id':'d','name':'deployment'},'device':{'status':'failure'}}]
    flags=['--raw'] if raw else []
    for args in [['devices','tags','list','one'],['groups','list'],['groups','devices','staging'],['devices','deployments','one']]:
        value=run([*args,*flags])
        assert value.exit_code==0,value.output
        assert value.output


def test_metadata_mutation_commands(cli_app):
    cli_app.api.add_group_devices.return_value={'matched_count':1,'updated_count':1}
    cli_app.api.remove_group_devices.return_value={'updated_count':1}
    for args in [['devices','tags','remove','one','site','--yes'],['groups','add','staging','one','--yes'],
                 ['groups','remove','staging','one','--yes']]:
        value=run(args)
        assert value.exit_code==0,value.output
    cli_app.api.remove_tag.assert_called_once_with('one','site')
    cli_app.api.remove_group_devices.assert_called_once_with('staging',['one'])
