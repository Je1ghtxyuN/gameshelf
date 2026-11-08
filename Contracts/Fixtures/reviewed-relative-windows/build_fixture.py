"""Deterministic synthetic Windows x64 PE; stdlib only, no compiler/download.

The only program actions are CreateDirectoryW(L"savedata"), CreateFileW on
L"savedata\\progress.txt", WriteFile of a fixed message, CloseHandle, Sleep,
and ExitProcess. No parameters, environment lookup, registry, network, helper
execution, runtime dependencies, or dynamic program/config loading.
"""
import struct
from pathlib import Path

MESSAGE = b'GameShelf synthetic relative fixture v1\r\n'


def build():
    data = bytearray(b'\0' * 40)  # one import descriptor and its null terminator
    labels = {}
    def add(name, value, alignment=1):
        data.extend(b'\0' * (-len(data) % alignment))
        labels[name] = 0x2000 + len(data)
        data.extend(value)
    names = ['CreateDirectoryW','CreateFileW','WriteFile','CloseHandle','Sleep','ExitProcess']
    add('dll', b'KERNEL32.dll\0')
    add('ilt', b'\0' * (8 * (len(names)+1)), 8)
    add('iat', b'\0' * (8 * (len(names)+1)), 8)
    for index, name in enumerate(names):
        add(name, b'\0\0' + name.encode() + b'\0', 2)
        for table in ('ilt','iat'):
            struct.pack_into('<Q', data, labels[table]-0x2000+8*index, labels[name])
        labels['call_'+name] = labels['iat']+8*index
    struct.pack_into('<IIIII', data, 0, labels['ilt'],0,0,labels['dll'],labels['iat'])
    add('directory', 'savedata\0'.encode('utf-16le'), 2)
    add('output', 'savedata\\progress.txt\0'.encode('utf-16le'), 2)
    add('message', MESSAGE)
    code = bytearray(); fixups = []; branches = []
    def emit(value): code.extend(bytes.fromhex(value))
    def rip(op, label):
        emit(op); fixups.append((len(code),label)); code.extend(b'\0'*4)
    def call(name): rip('ff15', 'call_'+name)
    def jump(op, label):
        emit(op); branches.append((len(code),label)); code.extend(b'\0'*4)
    # 32-byte shadow space, three stack arguments and local handles/counters.
    emit('4883ec58')
    rip('488d0d','directory'); emit('31d2'); call('CreateDirectoryW')
    rip('488d0d','output'); emit('ba00000040 4531c0 4531c9')
    emit('c744242002000000 c744242880000000 48c744243000000000')
    call('CreateFileW'); emit('4883f8ff'); jump('0f84','failure')
    emit('4889442440 4889c1'); rip('488d15','message')
    code.extend(b'\x41\xb8'+struct.pack('<I',len(MESSAGE)))
    emit('4c8d4c2448 48c744242000000000'); call('WriteFile')
    emit('89442450 488b4c2440'); call('CloseHandle')
    emit('8b442450 85c0'); jump('0f84','failure')
    emit('b934080000'); call('Sleep'); emit('31c9'); jump('e9','exit')
    branches_at = {'failure':len(code)}
    emit('b901000000'); branches_at['exit']=len(code)
    call('ExitProcess'); emit('cc')
    for offset, label in fixups:
        struct.pack_into('<i',code,offset,labels[label]-(0x1000+offset+4))
    for offset, label in branches:
        struct.pack_into('<i',code,offset,branches_at[label]-(offset+4))
    if max(len(code),len(data))>512: raise ValueError('Fixture section exceeded fixed bound')
    header=bytearray(512); header[:2]=b'MZ'; struct.pack_into('<I',header,0x3c,0x80)
    header[0x80:0x84]=b'PE\0\0'
    struct.pack_into('<HHIIIHH',header,0x84,0x8664,2,0,0,0,240,0x0022)
    opt=0x98
    struct.pack_into('<HBBIIIIIQII',header,opt,0x20b,0,0,512,512,0,0x1000,0x1000,0x140000000,4096,512)
    struct.pack_into('<HHHHHH',header,opt+40,6,0,0,0,6,0)
    struct.pack_into('<IIIIHH',header,opt+52,0,0x3000,512,0,3,0x0100)
    struct.pack_into('<QQQQII',header,opt+72,0x100000,0x1000,0x100000,0x1000,0,16)
    struct.pack_into('<II',header,opt+112+8,0x2000,40)
    struct.pack_into('<II',header,opt+112+12*8,labels['iat'],8*(len(names)+1))
    for index, (name, size, rva, raw, flags) in enumerate([
        (b'.text',len(code),0x1000,0x200,0x60000020),
        (b'.data',len(data),0x2000,0x400,0xc0000040)]):
        struct.pack_into('<8sIIIIIIHHI',header,opt+240+index*40,name,size,rva,512,raw,0,0,0,0,flags)
    return bytes(header+code.ljust(512,b'\0')+data.ljust(512,b'\0'))


if __name__ == '__main__':
    import argparse
    import hashlib
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    payload=build(); args.output.write_bytes(payload)
    print(hashlib.sha256(payload).hexdigest())
