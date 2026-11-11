"""One-shot pre-exec gate. Internal trusted child; NEVER a model-facing command.

Parent retains Popen, commits native PID and registers the pidfd before sending
32 unpredictable bytes. EOF/malformed data/parent loss means exit125, not exec.
The sealed launch data is bounded; both descriptors close before the original
native argv/environment is exec'd. No shell, fallback, signal or retry.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import select
import stat
import sys

LIMIT=1024*1024
TIMEOUT_MS=120000
# Linux ABI values (fcntl.h). The pinned uv standalone CPython 3.11 omits these
# fcntl names although the kernel and os.memfd_create support sealing. The gate
# stays self-contained (no factory imports) but follows the accepted
# linux_memfd_seals rule: a present but different export is an error.
F_GET_SEALS=1034
MEMFD_SEALS=0x1|0x2|0x4|0x8  # F_SEAL_SEAL|F_SEAL_SHRINK|F_SEAL_GROW|F_SEAL_WRITE

def need(ok):
    if not ok:raise ValueError('pre-exec gate refused')

def seal_constant():
    exported=getattr(fcntl,'F_GET_SEALS',None)
    need(exported is None or (type(exported) is int and exported==F_GET_SEALS))
    return F_GET_SEALS

def pairs(items):
    result={}
    for key,value in items:
        need(key not in result);result[key]=value
    return result

def exec_environment():
    # Exact block this process received from execve. Interpreter start-up may
    # rewrite os.environ (PEP 538 adds/changes LC_CTYPE for unset/C/POSIX
    # locales), but never the kernel's record of the original environment.
    fd=os.open('/proc/self/environ',os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        raw=b''
        while len(raw)<=LIMIT:
            chunk=os.read(fd,65536)
            if not chunk:break
            raw+=chunk
    finally:os.close(fd)
    need(len(raw)<=LIMIT and (not raw or raw.endswith(b'\0')))
    items=[]
    for item in raw[:-1].split(b'\0') if raw else ():
        key,sep,value=os.fsdecode(item).partition('=');need(bool(sep and key));items.append((key,value))
    return pairs(items)

def receive(fd, size):
    p=select.poll();p.register(fd,select.POLLIN|select.POLLHUP|select.POLLERR)
    need(bool(p.poll(TIMEOUT_MS)))
    raw=os.read(fd,size)
    return raw

def main():
    need(len(sys.argv)==4)
    planfd,gatefd=int(sys.argv[1]),int(sys.argv[2]);expected=sys.argv[3]
    need(planfd>2 and gatefd>2 and planfd!=gatefd and len(expected)==64)
    need(stat.S_ISFIFO(os.fstat(gatefd).st_mode))
    st=os.fstat(planfd);need(stat.S_ISREG(st.st_mode) and 0<st.st_size<=LIMIT)
    need(fcntl.fcntl(planfd,seal_constant())&MEMFD_SEALS==MEMFD_SEALS)
    os.lseek(planfd,0,os.SEEK_SET);raw=b''
    while len(raw)<st.st_size:
        chunk=os.read(planfd,min(65536,st.st_size-len(raw)));need(bool(chunk));raw+=chunk
    need(hashlib.sha256(raw).hexdigest()==expected)
    data=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False))
    need(type(data) is dict and set(data)=={'schema','argv','environment','cwd','parent_pid','release_sha256'})
    need(data['schema']=='sf-native-worker-exec-gate/v1' and type(data['parent_pid']) is int and data['parent_pid']==os.getppid())
    argv,env=data['argv'],data['environment']
    need(type(argv) is list and argv and all(type(x) is str and '\0' not in x for x in argv) and argv[0].startswith('/'))
    need(type(env) is dict and all(type(k) is str and type(v) is str and k and '=' not in k and '\0' not in k+v for k,v in env.items()))
    need(env==exec_environment() and data['cwd']==os.getcwd())
    # Pipe write is <=PIPE_BUF; read may be short, so bound total length without
    # accepting a prefix. The second read requires EOF: no replay/extra bytes.
    token=receive(gatefd,33)
    while len(token)<32:
        chunk=receive(gatefd,33-len(token));need(bool(chunk));token+=chunk
    need(len(token)==32 and receive(gatefd,1)==b'')
    need(hashlib.sha256(token).hexdigest()==data['release_sha256'] and data['parent_pid']==os.getppid())
    os.close(gatefd);os.close(planfd)
    os.execve(argv[0],argv,env)

if __name__=='__main__':
    try:main()
    except BaseException:
        # No argv/env, credentials, claim or token are written to diagnostics.
        try:os.write(2,b'FACTORY_PREEXEC_REFUSED\n')
        except OSError:pass
        os._exit(125)
