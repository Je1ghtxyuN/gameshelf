"""Exercise v2 over the real bounded HTTP handler with temporary source trees."""
import unittest
import uuid
import test_http


class UnifiedHTTPTests(unittest.TestCase):
    setUp = test_http.HTTPTests.setUp
    call = test_http.HTTPTests.call

    def test_scan_add_library_cas_events_and_legacy_session_over_http(self):
        owner=self.owner_token; player=self.token
        self.assertEqual(self.call('GET','/api/library')[0],401)
        root=self.s.add_root(str(self.source),'Fixture')
        self.assertEqual(self.call('POST',f'/api/admin/roots/{root["id"]}/scan',{},owner)[0],200)
        candidate=self.call('GET','/api/admin/candidates',token=owner)[1]['candidates'][0]
        add=dict(candidateIDs=[candidate['id']],userID=self.user['id'])
        self.assertEqual(self.call('POST','/api/admin/library/add',add,player)[0],403)
        status,result,_=self.call('POST','/api/admin/library/add',add,owner)
        self.assertEqual(status,200); game=result['games'][0]; gid=game['id']
        self.assertEqual(self.call('POST','/api/admin/library/add',add,owner)[1],result)
        self.assertEqual(self.call('GET','/api/library',token=player)[1]['games'],[game])
        self.assertEqual(self.call('GET','/api/library/roots',token=player)[1],dict(roots=[dict(id=root['id'],title='Fixture')]))
        path=f'/api/library/games/{gid}/metadata'
        self.assertEqual(self.call('POST',path,dict(revision=0,favorite=True),player)[0],200)
        self.assertEqual(self.call('POST',path,dict(revision=0,favorite=False),player)[0],409)
        event=dict(eventID=str(uuid.uuid4()),deviceID='windows',elapsedSeconds=60,endedAt=1000)
        path=f'/api/library/games/{gid}/playtime'
        for _ in range(2): self.assertEqual(self.call('POST',path,event,player)[1],dict(playtimeSeconds=60,lastPlayed=1000))
        self.assertEqual(self.call('POST',path,dict(event,elapsedSeconds=61),player)[0],409)
        self.assertEqual(self.call('POST',f'/api/admin/library/games/{gid}/variants',dict(variantIDs=[gid]),player)[0],403)
        self.assertEqual(self.call('POST',f'/api/library/games/{gid}/session',dict(variantID=gid,deviceID='mac',saveCompatibilityKey='unknown'),player)[0],409)
        status,lease,_=self.call('POST',f'/api/games/{gid}/session',dict(deviceID='old'),player)
        self.assertEqual(status,200)
        self.assertEqual(self.call('POST',f'/api/library/games/{gid}/session',dict(variantID=gid,deviceID='new'),player)[0],409)
        self.assertEqual(self.call('DELETE',f'/api/sessions/{lease["leaseID"]}',token=player)[0],200)
        status,session,_=self.call('POST',f'/api/library/games/{gid}/session',dict(variantID=gid,deviceID='new'),player)
        self.assertEqual(status,200); self.assertTrue(session['profileKey'].endswith('/'+gid))

    def test_windows_smb_route_preserves_default_and_lease_authority(self):
        root=self.s.add_root(str(self.source),'Fixture'); self.s.scan(root['id'])
        game=self.s.publish(self.s.candidates()[0]['id'])['id']
        self.s.grant(self.user['id'],game,True)
        path=f'/api/library/games/{game}/session'
        body=dict(variantID=game,deviceID='fixture')
        header={'X-GameShelf-Client-Platform':'windows'}
        self.assertEqual(self.call('POST',path,body,extra=header)[0],401)
        original_open=self.s.driver.open
        self.s.driver.open=lambda *args, **kwargs: dict(original_open(*args, **kwargs), port=1445)
        from gameshelf_server.__main__ import Settings
        for override in ('192.168.2.30','100.64.12.34'):
            cfg=Settings.from_env(dict(GAMESHELF_SOURCE_ROOTS='["/games"]',
                GAMESHELF_SMB_HOST='games.example.invalid',GAMESHELF_WINDOWS_SMB_HOST=override))
            self.server.application.windows_smb_host=cfg.windows_smb_host
            for route, data, headers, endpoint in (
                (path,body,None,('localhost',1445)),
                (path,body,header,(override,445)),
                (path,body,{'X-GameShelf-Client-Platform':'192.168.2.99'},('localhost',1445)),
                (f'/api/games/{game}/session',dict(deviceID='legacy'),header,('localhost',1445)),
            ):
                status,lease,_=self.call('POST',route,data,self.token,extra=headers)
                self.assertEqual(status,200)
                self.assertEqual((lease['share']['host'],lease['share']['port']),endpoint)
                self.assertEqual(lease['userID'],self.user['id'])
                self.assertEqual(self.call('DELETE',f'/api/sessions/{lease["leaseID"]}',token=self.token)[0],200)

    def test_account_candidate_flags_and_ignore_over_http(self):
        root=self.s.add_root(str(self.source),'Fixture')
        self.s.scan(root['id'])
        route='/api/library/candidates'
        self.assertEqual(self.call('GET',route)[0],401)
        self.assertEqual(self.call('GET',route,token=self.token)[0],403)
        status,result,_=self.call('GET',route,token=self.owner_token)
        self.assertEqual(status,200)
        row=result['candidates'][0]; cid=row['id']
        self.assertEqual((row['added'],row['ignored']),(False,False))
        body=dict(candidateIDs=[cid],ignored=True)
        self.assertEqual(self.call('POST',route+'/ignore',body,self.token)[0],403)
        self.assertEqual(self.call('POST',route+'/ignore',body,self.owner_token)[1]['candidates'][0]['ignored'],True)
        device_two=self.call('POST','/api/login',dict(username='owner',password='owner test password'))[1]['token']
        self.assertTrue(self.call('GET',route,token=device_two)[1]['candidates'][0]['ignored'])
        self.assertEqual(self.call('POST','/api/admin/library/add',dict(candidateIDs=[cid],userID=self.admin['id']),self.owner_token)[0],200)
        row=self.call('GET',route,token=device_two)[1]['candidates'][0]
        self.assertEqual((row['added'],row['ignored']),(True,False))
        legacy=self.call('GET','/api/admin/candidates',token=self.owner_token)[1]['candidates'][0]
        self.assertNotIn('added',legacy); self.assertNotIn('ignored',legacy)

    def test_preference_collection_cas_and_own_removal_over_http(self):
        root=self.s.add_root(str(self.source),'Fixture'); self.s.scan(root['id'])
        cid=self.s.candidates()[0]['id']
        body=dict(candidateIDs=[cid],userID=self.user['id'],entryPaths={cid:'Play.exe'})
        status,added,_=self.call('POST','/api/admin/library/add',body,self.owner_token)
        self.assertEqual(status,200); gid=added['games'][0]['id']
        self.assertEqual(added['games'][0]['preferredEntries'],dict(windows=dict(variantID=gid,entryPath='Play.exe')))
        pref=f'/api/library/games/{gid}/launch-preferences'
        self.assertEqual(self.call('POST',pref,dict(platform='macos',variantID=gid,entryPath='Play.exe'),self.token)[0],409)
        collection=str(uuid.uuid4())
        self.assertEqual(self.call('POST','/api/library/collections',dict(id=collection,name=' Group ',expectedName=None),self.token)[0],200)
        self.assertEqual(self.call('POST','/api/library/collections',dict(id=collection,name='Other',expectedName=None),self.token)[0],409)
        self.assertEqual(self.call('POST',f'/api/library/games/{gid}/metadata',dict(revision=0,collectionIDs=[collection],favorite=True),self.token)[0],200)
        self.assertEqual(self.call('DELETE',f'/api/library/collections/{collection}',token=self.token)[0],200)
        game=self.call('GET','/api/library',token=self.token)[1]['games'][0]
        self.assertEqual(game['revision'],2); self.assertEqual(game['collectionIDs'],[])
        self.assertEqual(self.call('DELETE',f'/api/library/games/{gid}',dict(userID=self.admin['id']),self.token)[0],400)
        self.assertEqual(self.call('DELETE',f'/api/library/games/{gid}',token=self.token)[0],200)
        self.assertEqual(self.call('GET','/api/library',token=self.token)[1]['games'],[])
        self.assertEqual(self.call('DELETE',f'/api/library/games/{gid}',token=self.token)[0],200)
        restored=self.call('POST','/api/admin/library/add',body,self.owner_token)[1]['games'][0]
        self.assertEqual(restored['revision'],2); self.assertTrue(restored['favorite'])
        self.assertEqual(restored['preferredEntries'],game['preferredEntries'])

    def test_reviewed_synthetic_optin_and_evidence_over_http(self):
        import runpy
        import test_reviewed_launch_profiles as fixtures
        source=self.source/'Synthetic'; source.mkdir(); (source/'savedata').mkdir()
        payload=runpy.run_path(str(fixtures.FIXTURE))['build']()
        (source/'Fixture.exe').write_bytes(payload)
        root=self.s.add_root(str(self.source),'Synthetic proof'); self.s.scan(root['id'])
        cid=next(c['id'] for c in self.s.candidates() if c['relative']=='Synthetic')
        game=self.s.publish(cid)['id']; self.s.grant(self.user['id'],game,True)
        self.s.driver=fixtures.ProofDriver(self.s.state)
        route=f'/api/admin/games/{game}/launch-profile'
        body=dict(reviewedProfileID=fixtures.PROFILE,enabled=True)
        self.assertEqual(self.call('POST',route,body,self.token)[0],403)
        self.assertEqual(self.call('POST',route,body,self.owner_token)[0],200)
        profile=self.call('GET','/api/admin/launch-profiles',token=self.owner_token)[1]['profiles'][0]
        status,lease,_=self.call('POST',f'/api/library/games/{game}/session',dict(variantID=game,deviceID='windows'),self.token)
        self.assertEqual(status,200)
        self.assertEqual(lease['windowsLaunch']['evidenceSHA256'],profile['evidenceSHA256'])
        self.assertTrue(lease['windowsLaunch']['synthetic'])
        self.assertEqual(self.call('POST',route,dict(body,enabled=False),self.owner_token)[0],409)
        self.assertEqual(self.call('DELETE',f'/api/library/games/{game}',token=self.token)[0],409)
        self.assertEqual(self.call('DELETE',f'/api/sessions/{lease["leaseID"]}',token=self.token)[0],200)

    def test_smb_enrollment_routes_are_opt_in_and_account_scoped(self):
        from gameshelf_server.smb_enrollment import SMBEnrollment, SMBEnrollmentAPI
        prefix = '/api/account/smb-enrollment'
        self.assertEqual(self.call('GET',prefix,token=self.token)[0],404)
        proofs = {}

        class Host:
            def resolve(self, username):
                return 'verified:'+username

            def deliver(self, principal, challenge, proof):
                proofs[challenge] = (principal,proof)

            def discard(self, challenge):
                proofs.pop(challenge,None)

            def provision(self, account, principal):
                return 'workspace-'+account[:8]

        self.s.smb_enrollment_api = SMBEnrollmentAPI(SMBEnrollment(self.s.db),Host(),'verify-nas')
        self.assertEqual(self.call('GET',prefix)[0],401)
        self.assertEqual(self.call('POST',prefix+'/workspace',{},self.token)[0],409)
        code, body, _ = self.call('POST',prefix+'/challenge',dict(username='member'),self.token)
        self.assertEqual(code,200)
        challenge = body['challengeID']
        self.assertEqual(body,dict(challengeID=challenge,share='verify-nas',filename=challenge+'.proof'))
        proof = proofs[challenge][1]
        payload = dict(challengeID=challenge,proof=proof)
        self.assertEqual(self.call('POST',prefix+'/proof',payload,self.owner_token)[0],403)
        self.assertEqual(self.call('POST',prefix+'/proof',payload,self.token)[0],200)
        self.assertNotIn(challenge,proofs)
        self.assertEqual(self.call('GET',prefix,token=self.token)[1],dict(state='verified'))
        self.assertEqual(self.call('POST',prefix+'/proof',payload,self.token)[0],403)
        code, ready, _ = self.call('POST',prefix+'/workspace',{},self.token)
        self.assertEqual(code,200)
        self.assertEqual(ready,dict(state='ready',share='workspace-'+self.user['id'][:8]))
        self.assertEqual(self.call('GET',prefix,token=self.token)[1],ready)
        self.assertEqual(self.call('GET',prefix,token=self.owner_token)[1],dict(state='unverified'))
