"""Actual localhost HTTP contracts, using only synthetic source/view fixtures."""
import threading
import unittest
import test_http
import test_save_namespaces
from namespace_support import KEY,PROFILE,RELATIVE
from gameshelf_server.http_api import make_server


class SaveNamespaceHTTPTests(unittest.TestCase):
    setUp=test_save_namespaces.SaveNamespaceTests.setUp
    reviewed=test_save_namespaces.SaveNamespaceTests.reviewed
    request=test_save_namespaces.SaveNamespaceTests.request
    denied=test_save_namespaces.SaveNamespaceTests.denied
    call=test_http.HTTPTests.call

    def test_capability_gate_six_fields_native_proof_and_old_route_share_namespace(self):
        mac,win,_=self.reviewed()
        self.server=make_server(self.s,'127.0.0.1',0); self.addCleanup(self.server.server_close)
        worker=threading.Thread(target=self.server.serve_forever,daemon=True); worker.start(); self.addCleanup(self.server.shutdown)
        activation=f'/api/admin/library/games/{mac}/save-compatibility'
        body=dict(variantIDs=[mac,win],compatibilityKey=KEY,reviewedProfileID=PROFILE)
        self.assertEqual(self.call('POST',activation,body,self.pt)[0],403)
        self.assertEqual(self.call('POST',activation,body,self.at)[0],200)
        self.assertEqual(self.call('GET','/api/admin/save-profiles',token=self.pt)[0],403)
        route=f'/api/library/games/{mac}/session'
        request=dict(variantID=mac,deviceID='Mac',saveCompatibilityKey=KEY)
        self.assertEqual(self.call('POST',route,request,self.pt)[0],409)
        request['adapter']='renpy-savedir-v1'
        self.assertEqual(self.call('POST',route,dict(request,args=['--unsafe']),self.pt)[0],400)
        status,first,_=self.call('POST',route,request,self.pt)
        self.assertEqual(status,200)
        self.assertEqual(set(first['saveNamespace']),{'key','relativePath','adapter','reviewedProfileID','revision','rulesSHA256'})
        self.assertEqual(first['renpyLaunch']['entryPath'],'.')
        self.assertEqual(first['saveNamespace']['relativePath'],RELATIVE)
        self.assertEqual(self.call('POST',f'/api/games/{win}/session',dict(deviceID='old aware',adapter='renpy-savedir-v1'),self.pt)[0],409)
        self.assertEqual(self.call('DELETE',f'/api/sessions/{first["leaseID"]}',token=self.pt)[0],200)
        self.assertEqual(self.call('POST',f'/api/games/{win}/session',dict(deviceID='old unaware'),self.pt)[0],409)
        status,second,_=self.call('POST',f'/api/games/{win}/session',dict(deviceID='old aware',adapter='renpy-savedir-v1'),self.pt)
        self.assertEqual(status,200)
        self.assertEqual(second['saveNamespace'],first['saveNamespace'])
        self.assertEqual(second['windowsLaunch']['relativeSavePath'],RELATIVE)
        self.assertEqual(second['windowsLaunch']['adapter'],'renpy-savedir-v1')
