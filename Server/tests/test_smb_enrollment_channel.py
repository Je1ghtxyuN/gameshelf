import io
import unittest

from gameshelf_server.smb_enrollment_channel import dispatch_host_request, read_message


class Host:
    def __init__(self):
        self.calls = []

    def resolve(self, username):
        self.calls.append(('resolve',username))
        return 'canonical'

    def deliver(self, principal, challenge, proof):
        self.calls.append(('deliver',principal,challenge,proof))

    def provision(self, account, principal):
        self.calls.append(('provision',account,principal))
        return 'workspace'


class EnrollmentChannelTests(unittest.TestCase):
    def test_only_fixed_operations_with_exact_fields_are_exposed(self):
        host = Host()
        self.assertEqual(dispatch_host_request(host,{'op':'resolve','username':'member'}),'canonical')
        self.assertIsNone(dispatch_host_request(host,{'op':'deliver','principal':'canonical',
                                                      'challenge':'a'*32,'proof':'b'*64}))
        self.assertEqual(dispatch_host_request(host,{'op':'provision','account':'a'*32,
                                                       'principal':'canonical'}),'workspace')
        for request in (
            {'op':'resolve','username':'member','password':'unexpected'},
            {'op':'provision','account':'a'*32,'principal':'canonical','path':'/etc'},
            {'op':'deliver','principal':'canonical','challenge':'a'*32,'proof':1},
            {'op':'exec','command':'fixture'},
        ):
            with self.assertRaises(ValueError):
                dispatch_host_request(host,request)
        self.assertEqual(len(host.calls),3)

    def test_frame_rejects_duplicate_keys_truncation_and_oversize(self):
        for message in (b'{"op":"resolve","op":"provision"}\n',b'{}',b'[]\n',b' '*4097+b'\n'):
            with self.assertRaises(ValueError):
                read_message(io.BytesIO(message))
        self.assertEqual(read_message(io.BytesIO(b'{"op":"resolve"}\n')),{'op':'resolve'})
