"""Historical edition identities over the real authenticated HTTP boundary."""
import json
import unittest
import test_http


class AdminEditionsHTTPTests(unittest.TestCase):
    setUp=test_http.HTTPTests.setUp
    call=test_http.HTTPTests.call

    def test_admin_only_relative_identity_and_legacy_game_response_unchanged(self):
        path='/api/admin/library/editions'
        self.assertEqual(self.call('GET',path)[0],401)
        self.assertEqual(self.call('GET',path,token=self.token)[0],403)
        root=self.s.add_root(str(self.source),'Fixture'); self.s.scan(root['id'])
        candidate=self.s.candidates()[0]; game=self.s.publish(candidate['id'])
        before=self.call('GET','/api/admin/games',token=self.owner_token)[1]
        status,response,headers=self.call('GET',path,token=self.owner_token)
        self.assertEqual(status,200)
        row=response['editions'][0]
        self.assertEqual((row['id'],row['canonicalID'],row['candidateID'],row['rootID']),
                         (game['id'],game['id'],candidate['id'],root['id']))
        self.assertEqual(set(row),{'id','canonicalID','candidateID','rootID','relative','signature','entries','savePaths'})
        self.assertEqual((row['relative'],row['entries'],row['savePaths']),('Example',game['entries'],game['savePaths']))
        self.assertNotIn(str(self.source),json.dumps(response))
        self.assertNotIn(str(self.s.state),json.dumps(response))
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual(self.call('GET','/api/admin/games',token=self.owner_token)[1],before)
