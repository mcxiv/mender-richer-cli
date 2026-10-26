"""TUI workflows exercise real controller logic with scripted picker choices."""

from pathlib import Path
from unittest.mock import Mock

import pytest

import main


def choice(index=None, *, multi=False):
    return ([] if index is None else [index], -1, None, multi)


@pytest.fixture
def tui(cli_app,monkeypatch):
    cli_app.api.devices.return_value=[{'id':'one','attributes':[{'scope':'tags','name':'name','value':'One'}]},
                                     {'id':'two','attributes':[{'scope':'tags','name':'name','value':'Two'}]}]
    cli_app.api.inventory.return_value={'id':'one','attribute':'value'}
    cli_app.api.tags.return_value=[{'name':'site','value':'Paris'}]
    cli_app.api.groups.return_value=['old','new']
    cli_app.api.group_devices.return_value=['two']
    cli_app.api.device_group.return_value='old'
    cli_app.api.add_group_devices.return_value={'matched_count':1,'updated_count':1}
    cli_app.api.remove_group_devices.return_value={'updated_count':1}
    cli_app.api.deployment_details.return_value=({'id':'d'},{'failure':1})
    cli_app.api.deployment_log.return_value='failure details'
    cli_app.api.all_pages.return_value=[{'id':'d','name':'deployment'}]
    cli_app.api.device_deployments.return_value=[{'deployment':{'id':'d','name':'deployment'},'device':{'status':'failure'}}]
    monkeypatch.setattr(main,'show_text',Mock())
    monkeypatch.setattr(main,'confirm',lambda *_:True)
    return cli_app


def test_devices_refresh_marking_and_favorites(tui,monkeypatch):
    favorites=Mock()
    favorites.get.return_value={'id':'one'}
    monkeypatch.setattr(main,'Favorites',Mock(return_value=favorites))
    count=0
    def picker(*args,**kwargs):
        nonlocal count
        count+=1
        if count==1:
            kwargs['toggle_favorite'](1)
            return choice(1,multi=True)
        return choice(0) if count==2 else choice()
    monkeypatch.setattr(main,'pick',picker)
    deploy=Mock()
    menu=Mock()
    monkeypatch.setattr(main,'_interactive_deploy',deploy)
    monkeypatch.setattr(main,'_device_menu',menu)
    main._interactive_devices(tui)
    favorites.toggle.assert_called_once_with('https://example.com',{'id':'two','name':'Two'})
    deploy.assert_called_once_with(tui,['two'])
    assert menu.call_args.args[1]['id']=='one'


@pytest.mark.parametrize('group',[None,'empty'])
def test_empty_device_lists(tui,monkeypatch,group):
    tui.api.devices.return_value=[]
    main._interactive_devices(tui,group)
    main.show_text.assert_called_once()


def test_group_device_browser_filters_members(tui,monkeypatch):
    picker=Mock(return_value=choice())
    monkeypatch.setattr(main,'pick',picker)
    main._interactive_devices(tui,'old')
    assert [row[1] for row in picker.call_args.args[2]]==['two']


@pytest.mark.parametrize('index',[0,1,2,3,4,5,7,8,9,10,11])
def test_device_menu_actions(tui,monkeypatch,tmp_path,index):
    device=tui.api.devices.return_value[0]
    file=tmp_path/'file'
    file.write_text('content')
    values={1:['8080:80'],2:[file,'/tmp/file'],3:['/tmp/file',tmp_path]}
    monkeypatch.setattr(main.click,'prompt',Mock(side_effect=values.get(index,[])))
    monkeypatch.setattr(main.remote,'terminal',Mock())
    monkeypatch.setattr(main.remote,'port_forward',Mock())
    monkeypatch.setattr(main,'_interactive_deploy',Mock())
    monkeypatch.setattr(main,'_interactive_tags',Mock())
    monkeypatch.setattr(main,'_interactive_change_group',Mock())
    monkeypatch.setattr(main,'_interactive_deployments',Mock())
    selections=[choice(index),choice()]+([choice()] if index==5 else [])
    monkeypatch.setattr(main,'pick',Mock(side_effect=selections))
    if index==11:
        with pytest.raises(SystemExit):
            main._device_menu(tui,device,[device])
    else:
        main._device_menu(tui,device,[device])
    expected={0:main.remote.terminal,1:main.remote.port_forward,2:tui.api.upload_file,3:tui.api.download_file,
              4:main._interactive_deploy,5:tui.api.inventory,7:main._interactive_tags,
              8:main._interactive_change_group,9:main._interactive_deployments}
    if index in expected:
        expected[index].assert_called_once()


def test_device_menu_operational_errors_stay_in_tui(tui,monkeypatch):
    monkeypatch.setattr(main,'pick',Mock(side_effect=[choice(0),choice()]))
    monkeypatch.setattr(main.remote,'terminal',Mock(side_effect=RuntimeError('offline')))
    main._device_menu(tui,tui.api.devices.return_value[0],[])
    main.show_text.assert_called_once_with('Error','offline')


@pytest.mark.parametrize(('action','confirmed'),[(0,True),(1,True),(1,False),(None,True)])
def test_tag_editor_existing_tag(tui,monkeypatch,action,confirmed):
    monkeypatch.setattr(main,'pick',Mock(side_effect=[choice(1),choice(action),choice()]))
    monkeypatch.setattr(main.click,'prompt',Mock(return_value='London'))
    monkeypatch.setattr(main,'confirm',lambda *_:confirmed)
    main._interactive_tags(tui,'one')
    if action==0:
        tui.api.set_tags.assert_called_once_with('one',{'site':'London'})
    elif action==1 and confirmed:
        tui.api.remove_tag.assert_called_once_with('one','site')
    else:
        tui.api.remove_tag.assert_not_called()


@pytest.mark.parametrize(('index','current','confirmed','changed'),[
    (None,'old',True,None),(0,'old',True,'remove'),(0,None,True,None),
    (1,'old',True,'assign'),(2,'old',True,None),(3,'old',False,None),(3,'old',True,'assign')])
def test_change_group_paths(tui,monkeypatch,index,current,confirmed,changed):
    tui.api.device_group.return_value=current
    monkeypatch.setattr(main,'pick',Mock(return_value=choice(index)))
    monkeypatch.setattr(main.click,'prompt',Mock(return_value='created'))
    monkeypatch.setattr(main,'confirm',lambda *_:confirmed)
    main._interactive_change_group(tui,'one')
    assert tui.api.remove_group_devices.called is (changed=='remove')
    assert tui.api.assign_group.called is (changed=='assign')


@pytest.mark.parametrize(('members','selected','expected'),[(None,1,['two']),({'one'},0,['one']),({'missing'},None,[]),(None,None,[])])
def test_device_selection_for_membership(tui,monkeypatch,members,selected,expected):
    monkeypatch.setattr(main,'pick',Mock(return_value=choice(selected)))
    assert main._choose_devices(tui,'Select devices',members)==expected


@pytest.mark.parametrize('confirmed',[False,True])
def test_create_group(tui,monkeypatch,confirmed):
    monkeypatch.setattr(main,'pick',Mock(side_effect=[choice(0),choice()]))
    monkeypatch.setattr(main.click,'prompt',Mock(return_value='created'))
    monkeypatch.setattr(main,'_choose_devices',Mock(return_value=['one']))
    monkeypatch.setattr(main,'confirm',lambda *_:confirmed)
    main._interactive_groups(tui)
    assert tui.api.add_group_devices.called is confirmed


@pytest.mark.parametrize('action',[0,1,2,None])
def test_existing_group_actions(tui,monkeypatch,action):
    monkeypatch.setattr(main,'pick',Mock(side_effect=[choice(1),choice(action),choice()]))
    monkeypatch.setattr(main,'_choose_devices',Mock(return_value=['one']))
    monkeypatch.setattr(main,'_interactive_devices',Mock())
    main._interactive_groups(tui)
    if action==0:
        main._interactive_devices.assert_called_once_with(tui,'old')
    elif action==1:
        tui.api.add_group_devices.assert_called_once_with('old',['one'])
    elif action==2:
        tui.api.remove_group_devices.assert_called_once_with('old',['one'])


def test_group_without_members_is_not_deployed(tui,monkeypatch):
    tui.api.group_devices.return_value=[]
    monkeypatch.setattr(main,'pick',Mock(side_effect=[choice(1),choice(3),choice()]))
    main._interactive_groups(tui)
    tui.api.deploy.assert_not_called()
    assert 'no deployment' in main.show_text.call_args.args[1]


@pytest.mark.parametrize('device',[None,'one'])
@pytest.mark.parametrize('empty',[False,True])
def test_deployment_browser(tui,monkeypatch,device,empty):
    if empty:
        tui.api.all_pages.return_value=[]
        tui.api.device_deployments.return_value=[]
    monkeypatch.setattr(main,'pick',Mock(side_effect=[choice(0),choice()]))
    monkeypatch.setattr(main,'_interactive_deployment',Mock())
    main._interactive_deployments(tui,device)
    assert main._interactive_deployment.called is not empty


@pytest.mark.parametrize('action',[0,1,2,None])
def test_deployment_actions(tui,monkeypatch,action):
    selections=[choice(action),choice()]+([choice()] if action==0 else [])
    monkeypatch.setattr(main,'pick',Mock(side_effect=selections))
    monkeypatch.setattr(main,'_interactive_deployment_devices',Mock())
    main._interactive_deployment(tui,{'id':'d','name':'deployment'},'one')
    if action==0:
        tui.api.deployment_details.assert_called_once_with('d')
    elif action==1:
        main._interactive_deployment_devices.assert_called_once_with(tui,'d')
    elif action==2:
        tui.api.deployment_log.assert_called_once_with('d','one')


def test_empty_or_cancelled_device_results(tui,monkeypatch):
    monkeypatch.setattr(main,'pick',Mock(return_value=choice()))
    main._interactive_deployment_devices(tui,'d')
    tui.api.deployment_devices.assert_not_called()
    tui.api.deployment_devices.return_value=[]
    monkeypatch.setattr(main,'pick',Mock(return_value=choice(0)))
    main._interactive_deployment_devices(tui,'d')
    assert 'checking for updates' in main.show_text.call_args.args[1]


@pytest.mark.parametrize('stage',['empty','order-cancel','artifact-cancel','decline','name','type'])
def test_interactive_artifact_deployment(tui,monkeypatch,stage):
    tui.api.artifacts.return_value=[] if stage=='empty' else [
        {'name':'z','type_info':{'type':'a'}},{'name':'a','type_info':{'type':'z'}}]
    choices=[choice() if stage=='order-cancel' else choice(1 if stage=='type' else 0),
             choice() if stage=='artifact-cancel' else choice(0)]
    monkeypatch.setattr(main,'pick',Mock(side_effect=choices))
    monkeypatch.setattr(main,'confirm',lambda *_:stage!='decline')
    main._interactive_deploy(tui,['one','two'])
    assert tui.api.deploy.called is (stage in {'name','type'})
    if stage in {'name','type'}:
        assert tui.api.deploy.call_args.args==('z' if stage=='type' else 'a',['one','two'])


def test_root_devices_and_error_recovery(tui,monkeypatch):
    monkeypatch.setattr(main,'pick',Mock(side_effect=[choice(0),choice()]))
    monkeypatch.setattr(main,'_interactive_devices',Mock(side_effect=RuntimeError('offline')))
    main.interactive(tui)
    main.show_text.assert_called_once_with('Error','offline')
