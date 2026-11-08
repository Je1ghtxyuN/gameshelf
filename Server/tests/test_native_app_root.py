"""An explicitly registered native .app root is a real dot launcher, not unknown."""
import json
import unittest
import test_unified_library


class NativeAppRootTests(unittest.TestCase):
    setUp=test_unified_library.UnifiedLibraryTests.setUp
    request=test_unified_library.UnifiedLibraryTests.request
    denied=test_unified_library.UnifiedLibraryTests.denied
    library=test_unified_library.UnifiedLibraryTests.library
    publish=test_unified_library.UnifiedLibraryTests.publish

    def registered(self,*,app_as_root=False):
        folder=self.source/'Native.app'
        (folder/'Contents/Resources/autorun/renpy').mkdir(parents=True)
        (folder/'Contents/Info.plist').write_text('synthetic native app metadata')
        root=self.s.add_root(str(folder),'Native fixture root') if app_as_root else self.root
        registered=self.request('POST',f'/api/admin/roots/{root["id"]}/candidates',
            dict(games=[dict(relative='.' if app_as_root else 'Native.app',entryPath='.')]),self.at)
        candidate=registered['candidates'][0]
        self.assertEqual(candidate['entries'],[dict(path='.',backend='native',engine='renpy')])
        return candidate

    def test_registered_dot_native_app_displays_adds_prefers_and_sessions(self):
        candidate=self.registered()
        added=self.request('POST','/api/admin/library/add',dict(candidateIDs=[candidate['id']],userID=self.a['id'],entryPaths={candidate['id']:'.'}),self.at)
        game=added['games'][0]; variant=game['variants'][0]
        self.assertEqual((variant['platform'],variant['entries'][0]['path']),('macos','.'))
        self.assertEqual(game['preferredEntries'],dict(macos=dict(variantID=variant['id'],entryPath='.')))
        self.request('POST',f'/api/library/games/{game["id"]}/launch-preferences',dict(platform='macos',variantID=variant['id'],entryPath='.'))
        session=self.request('POST',f'/api/library/games/{game["id"]}/session',dict(variantID=variant['id'],deviceID='mac'))
        self.assertEqual(session['game']['entries'][0]['path'],'.')
        self.s.release(self.a,session['leaseID'])
        self.s.session(self.a,variant['id'],'legacy native')

    def test_app_itself_registered_as_root_dot_relative_stays_macos_offline(self):
        candidate=self.registered(app_as_root=True); game=self.s.publish(candidate['id'])
        self.s.grant(self.a['id'],game['id'],True)
        folder=self.source/'Native.app'; folder.rename(self.source/'Native-offline.app')
        variant=self.library()['games'][0]['variants'][0]
        self.assertEqual(variant['platform'],'macos')
        self.assertEqual(variant['entries'][0]['path'],'.')

    def test_dot_never_means_wine_web_or_non_app_native(self):
        candidate=self.registered(); game=self.s.publish(candidate['id'])
        self.s.grant(self.a['id'],game['id'],True)
        for backend in ('wine','web','rpgmz'):
            self.s.db.execute('UPDATE games SET entries=? WHERE id=?',(json.dumps([dict(path='.',backend=backend,engine='unknown')]),game['id']))
            self.assertEqual(self.library()['games'][0]['variants'],[])
            self.denied(lambda:self.request('POST',f'/api/library/games/{game["id"]}/launch-preferences',dict(platform='macos',variantID=game['id'],entryPath='.')),400)
        self.s.scan(self.root['id']); normal=next(c for c in self.s.candidates() if c['relative']=='Novel')
        other=self.s.publish(normal['id']); self.s.grant(self.a['id'],other['id'],True)
        self.s.db.execute('UPDATE games SET entries=? WHERE id=?',(json.dumps([dict(path='.',backend='native',engine='unknown')]),other['id']))
        self.denied(lambda:self.request('POST',f'/api/library/games/{other["id"]}/launch-preferences',dict(platform='macos',variantID=other['id'],entryPath='.')),400)
