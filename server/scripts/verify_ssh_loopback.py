#!/usr/bin/env python3
"""G5.2 manually invoked real OpenSSH loopback acceptance (never remote Internet).

Creates ephemeral SSH host/auth keys and starts unprivileged sshd bound strictly
to 127.0.0.1 on a random port. All state and processes are cleaned on exit.
This script is not run automatically by CI because sshd may be unavailable.
"""
import base64
import hashlib
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge.core import Store
from bridge.ssh_remote import SSHManager


def keygen(target):
    subprocess.run(
        ['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(target)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=10,
    )
    target.chmod(0o600)


def check_session(manager, key, *, expect_connected, marker,
                  failure_substring='Host key verification failed'):
    opened = manager.open('loopback', request_key=key)
    session_id = opened['session_id']
    if opened['connection_state'] != 'unverified':
        raise AssertionError('Opening local SSH process cannot attest to live handshake')
    if expect_connected:
        manager.write(
            session_id, f"printf '{marker}\\n'\n", request_key=key + '-stdin'
        )
    output = ''
    final = None
    for _ in range(120):
        status = manager.status(session_id)
        output = manager.read(session_id, cursor=0, max_bytes=65536)['output']
        if expect_connected:
            # Command string and resulting shell output are both observable in
            # interactive PTY; this is paired with actual server-side auth log.
            if output.count(marker) >= 2:
                break
        elif not status['active']:
            final = status
            break
        time.sleep(.075)
    if expect_connected:
        if output.count(marker) < 2:
            raise AssertionError('Verified remote shell output not observed')
        result = manager.close(session_id)
        if result['active']:
            raise AssertionError('Cannot confirm terminal closure')
    else:
        if final is None or final['exit_code'] != 255:
            raise AssertionError('Expected rejected SSH host key exit 255')
        if failure_substring not in output:
            raise AssertionError('Expected actual OpenSSH authentication rejection: ' +
                                 failure_substring)
    if manager.store.unknown_app_writers():
        raise AssertionError('Authoritative terminal exit did not release writer claim')
    return output


def main():
    if not shutil.which('sshd') and not Path('/usr/sbin/sshd').is_file():
        raise RuntimeError('Local sshd binary required for this opt-in acceptance')
    sshd_binary = shutil.which('sshd') or '/usr/sbin/sshd'
    if not shutil.which('ssh-keygen') or not shutil.which('ssh'):
        raise RuntimeError('OpenSSH tools required')
    with tempfile.TemporaryDirectory(prefix='codex-x-ssh-g52-') as directory:
        root = Path(directory).resolve()
        project = root / 'project'
        project.mkdir()
        auth = root / 'auth'
        auth.mkdir(mode=0o700)
        for key in ('host', 'identity', 'wrong_host', 'wrong_identity'):
            keygen(root / key)
        auth_keys = auth / 'authorized_keys'
        auth_keys.write_bytes((root / 'identity.pub').read_bytes())
        auth_keys.chmod(0o600)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        def host_pin(name):
            public = (root / (name + '.pub')).read_text().split()
            out = root / ('known_' + name)
            out.write_text(f'[127.0.0.1]:{port} {public[0]} {public[1]}\n')
            out.chmod(0o600)
            return out
        good = host_pin('host')
        wrong = host_pin('wrong_host')
        cfg = root / 'sshd_config'
        cfg.write_text('\n'.join([
            f'Port {port}', 'ListenAddress 127.0.0.1',
            f'HostKey {root / "host"}', f'PidFile {root / "pid"}',
            f'AuthorizedKeysFile {auth_keys}', 'PasswordAuthentication no',
            'KbdInteractiveAuthentication no', 'PubkeyAuthentication yes',
            'UsePAM no', 'PermitRootLogin no', 'AllowTcpForwarding no',
            'AllowAgentForwarding no', 'X11Forwarding no', 'PrintMotd no',
            'LogLevel VERBOSE', 'Subsystem sftp internal-sftp', '',
        ]))
        subprocess.run([sshd_binary, '-t', '-f', str(cfg)], check=True, timeout=8)
        daemon = subprocess.Popen(
            [sshd_binary, '-D', '-e', '-f', str(cfg)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        store = Store(root / 'state.sqlite3')
        profile = {
            'project_id': 'demo', 'host': '127.0.0.1',
            'user': os.environ['USER'], 'port': port,
            'known_hosts_file': str(good), 'identity_file': str(root / 'identity'),
        }
        settings = {
            'projects': {'demo': {'cwd': str(project), 'allow_write': True}},
            'shell': {'enabled': True, 'executable': '/bin/sh'},
            'ssh': {
                'enabled': True, 'executable': shutil.which('ssh'),
                'profiles': {'loopback': profile},
            },
        }
        manager = SSHManager(settings, store)
        try:
            time.sleep(.25)
            marker = 'G52_REMOTE_' + os.urandom(5).hex().upper()
            output = check_session(manager, 'valid-host-auth', expect_connected=True, marker=marker)
            print('PINNED_HOST_AND_REMOTE_PTY', 'PASS', 'output_sha256',
                  hashlib.sha256(output.encode('utf-8')).hexdigest())
            profile['known_hosts_file'] = str(wrong)
            rejected = check_session(manager, 'wrong-host-key', expect_connected=False, marker='')
            print('WRONG_HOST_KEY_REJECTED', 'PASS', 'exit=255')
            profile['known_hosts_file'] = str(good)
            profile['identity_file'] = str(root / 'wrong_identity')
            check_session(manager, 'wrong-identity', expect_connected=False,
                          marker='', failure_substring='Permission denied')
            print('WRONG_PUBLIC_KEY_REJECTED', 'PASS', 'exit=255')
            profile['identity_file'] = str(root / 'identity')
        finally:
            manager.shutdown()
            store.close()
            daemon.terminate()
            try:
                daemon.wait(timeout=3)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait()
        logs = (daemon.stderr.read() or b'').decode('utf-8', 'replace')
        if 'Accepted publickey for ' + os.environ['USER'] not in logs:
            raise AssertionError('Server did not attest successful public-key login')
        print('SERVER_PUBLIC_KEY_AUTHENTICATION', 'PASS')
        print('ACCEPTANCE_SCOPE', 'temporary localhost sshd only, no external host')


if __name__ == '__main__':
    main()
