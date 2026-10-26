"""Exercise real HTTP serialization and streaming against an isolated loopback server."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from urllib.parse import urlsplit

import pytest
import requests

from api import MenderAPI

REAL_REQUEST = requests.sessions.Session.request


@pytest.fixture
def server(monkeypatch, api_client):
    state={'tags':{'site':'Paris','name':'Bench'}, 'revision':1, 'requests':[], 'payload':b'\x00\xffcontent\r\n'}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_GET(self):
            self.handle_request()
        def do_PATCH(self):
            self.handle_request()
        def do_PUT(self):
            self.handle_request()
        def handle_request(self):
            path=urlsplit(self.path).path
            body=self.rfile.read(int(self.headers.get('Content-Length',0)))
            state['requests'].append((self.command,path,dict(self.headers),body))
            status=200
            headers={}
            if path=='/object':
                value=state['payload']
            elif path.endswith('/artifacts/artifact/download'):
                value={'uri':f'http://127.0.0.1:{self.server.server_port}/object'}
            elif path.endswith('/artifacts/artifact'):
                value={'name':'release','size':len(state['payload'])}
            elif path.endswith('/inventory/devices/device'):
                value={'attributes':[{'scope':'tags','name':name,'value':value} for name,value in state['tags'].items()]}
                headers['ETag']=f'"{state["revision"]}"'
            elif path.endswith('/inventory/devices/device/tags'):
                tags=json.loads(body)
                if self.command=='PATCH':
                    state['tags'].update({tag['name']:tag['value'] for tag in tags})
                elif self.headers.get('If-Match')==f'"{state["revision"]}"':
                    state['tags']={tag['name']:tag['value'] for tag in tags}
                else:
                    status=412
                state['revision']+=1
                value=b''
            elif path.endswith('/deviceconnect/devices/device'):
                value={'status':'connected'}
            elif path.endswith('/deviceconnect/devices/device/upload'):
                status=201
                value=b''
            elif path.endswith('/deviceconnect/devices/device/download'):
                value=state['payload']
                headers['X-MEN-FILE-SIZE']=str(len(value))
            else:
                status=404
                value={'error':'unexpected endpoint'}
            encoded=value if isinstance(value,bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Length',str(len(encoded)))
            self.send_header('Content-Type','application/octet-stream' if isinstance(value,bytes) else 'application/json')
            for key,value in headers.items():
                self.send_header(key,value)
            self.end_headers()
            self.wfile.write(encoded)
    service=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=lambda:service.serve_forever(poll_interval=0.01),daemon=True)
    thread.start()
    def loopback_only(session,method,url,*args,**kwargs):
        assert urlsplit(url).hostname=='127.0.0.1','integration tests must stay on loopback'
        return REAL_REQUEST(session,method,url,*args,**kwargs)
    monkeypatch.setattr(requests.sessions.Session,'request',loopback_only)
    monkeypatch.setenv('NO_PROXY','127.0.0.1')
    monkeypatch.setenv('no_proxy','127.0.0.1')
    api_client.config.server=f'http://127.0.0.1:{service.server_port}'
    api_client.session.trust_env=False
    del api_client.session.request
    try:
        yield api_client,state
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=5)


@pytest.mark.integration
def test_real_tag_updates_preserve_unrelated_tags_and_use_etag(server):
    client,state=server
    client.rename_device('device','Renamed')
    assert state['tags']=={'site':'Paris','name':'Renamed'}
    client.remove_tag('device','site')
    assert state['tags']=={'name':'Renamed'}
    mutation=state['requests'][-1]
    assert mutation[0]=='PUT' and mutation[2]['If-Match']=='"2"'


@pytest.mark.integration
def test_signed_artifact_download_never_sends_management_credentials(server,tmp_path):
    client,state=server
    destination=client.download_artifact('artifact',tmp_path,False)
    assert destination.read_bytes()==state['payload']
    signed=[request for request in state['requests'] if request[1]=='/object'][0]
    assert 'Authorization' not in signed[2]
    management=[request for request in state['requests'] if request[1]!='/object']
    assert all(r[2]['Authorization']=='Bearer test-token' for r in management)


@pytest.mark.integration
def test_real_multipart_upload_and_device_download(server,tmp_path):
    client,state=server
    path=tmp_path/'source.bin'
    path.write_bytes(state['payload'])
    client.upload_file('device',path,'/tmp/destination.bin')
    upload=[request for request in state['requests'] if request[0]=='PUT'][0]
    assert upload[2]['Content-Type'].startswith('multipart/form-data; boundary=')
    assert state['payload'] in upload[3]
    assert b'/tmp/destination.bin' in upload[3]
    output=client.download_file('device','/tmp/destination.bin',tmp_path/'download.bin')
    assert output.read_bytes()==state['payload']
