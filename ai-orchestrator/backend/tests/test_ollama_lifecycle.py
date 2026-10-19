"""Execute the real entrypoint with fixture children; never run/download models."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

import pytest

ADDON = Path(__file__).parents[2]


@pytest.fixture
def runtime(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    app = tmp_path / "app"
    (app / "backend").mkdir(parents=True)
    config = tmp_path / "config"
    config.mkdir()
    events = tmp_path / "events"
    backend = bin_dir / "backend"
    backend.write_text('''#!/usr/bin/env python3
import json, os, signal, time
from pathlib import Path
p=Path(os.environ['EVENTS'])
p.open('a').write(json.dumps({'backend':os.getpid(),'host':os.environ['OLLAMA_HOST']})+'\\n')
signal.signal(signal.SIGTERM, lambda *_: exit(0))
if os.getenv('BACKEND_FAIL'): exit(7)
while True: time.sleep(.05)
''')
    backend.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", CONFIG_PATH=str(tmp_path / "options.json"),
               DATA_DIR=str(tmp_path / "data"), APP_DIR=str(app), ADDON_CONFIG_DIR=str(config),
               BACKEND_EXECUTABLE=str(backend), EVENTS=str(events), OLLAMA_START_TIMEOUT="2",
               OLLAMA_KV_CACHE_TYPE="q4_0", HOME="")
    procs = []

    def start(options=None, **extra):
        (tmp_path / "options.json").write_text(json.dumps(options or {"llm_provider": "openai", "enable_rag": False}))
        p = subprocess.Popen(["bash", str(ADDON / "run.sh")], env=dict(env, **extra), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, start_new_session=True)
        procs.append(p)
        return p

    def records():
        return [json.loads(x) for x in events.read_text().splitlines()] if events.exists() else []

    yield start, records, tmp_path, bin_dir
    for p in procs:
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
        p.communicate(timeout=3)


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.03)
    assert predicate(), "fixture event did not occur"


def install_ollama(bin_dir):
    script = bin_dir / 'ollama'
    script.write_text('''#!/usr/bin/env python3
import json, os, signal, sys, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
Path(os.environ['EVENTS']).open('a').write(json.dumps(dict(command=sys.argv[1:], pid=os.getpid(), **{k:os.getenv(k) for k in ['HOME','OLLAMA_MODELS','OLLAMA_HOST','OLLAMA_KV_CACHE_TYPE']}))+'\\n')
if sys.argv[1] == 'serve':
    if os.getenv('SERVE_FAIL'): exit(9)
    if os.getenv('SERVE_HANG'):
        while True: time.sleep(.05)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
        def log_message(self, *args): pass
    host=urlparse(os.environ['OLLAMA_HOST'])
    HTTPServer((host.hostname, host.port), Handler).serve_forever()
elif sys.argv[1] == 'list': print('NAME ID SIZE MODIFIED\\ngemma4:e4b x x x')
''')
    script.chmod(0o755)


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def test_managed_server_has_persistent_cpu_safe_environment_and_custom_port(runtime):
    start, records, root, bin_dir = runtime
    install_ollama(bin_dir)
    port = free_port()
    p = start({'llm_provider':'openai', 'enable_rag':True, 'internal_ollama_port':port})
    wait_for(lambda: any('backend' in r for r in records()) or p.poll() is not None)
    assert p.poll() is None, p.communicate()[0]
    serve = next(r for r in records() if r.get('command') == ['serve'])
    assert serve['HOME'] == str(root / 'data/ollama/home')
    assert serve['OLLAMA_MODELS'] == str(root / 'data/ollama/models')
    assert Path(serve['HOME']).is_dir() and Path(serve['OLLAMA_MODELS']).is_dir()
    assert serve['OLLAMA_KV_CACHE_TYPE'] == 'f16'
    assert serve['OLLAMA_HOST'] == f'http://127.0.0.1:{port}'
    assert next(r for r in records() if 'backend' in r)['host'] == serve['OLLAMA_HOST']
    p.terminate()
    assert p.wait(timeout=4) == 0
    with pytest.raises(ProcessLookupError): os.kill(serve['pid'], 0)


def test_occupied_port_is_refused_without_starting_or_killing_listener(runtime):
    start, records, _, bin_dir = runtime
    install_ollama(bin_dir)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        p = start({'internal_ollama_port':port})
        output = p.communicate(timeout=4)[0]
        assert p.returncode != 0 and 'refusing to reuse' in output
        assert not records()
        assert listener.getsockname()[1] == port


@pytest.mark.parametrize('extra,diagnostic', [({'SERVE_FAIL':'1'}, 'Managed Ollama exited'), ({'SERVE_HANG':'1'}, 'readiness timeout')])
def test_startup_failure_never_starts_backend(runtime, extra, diagnostic):
    start, records, _, bin_dir = runtime
    install_ollama(bin_dir)
    p = start({'internal_ollama_port':free_port()}, **extra)
    output = p.communicate(timeout=5)[0]
    assert p.returncode != 0 and diagnostic in output
    assert not any('backend' in r for r in records())
    for r in records():
        with pytest.raises(ProcessLookupError): os.kill(r['pid'], 0)


def test_dead_child_is_not_masked_by_successful_http_probe(runtime):
    start, records, _, bin_dir = runtime
    install_ollama(bin_dir)
    curl = bin_dir / 'curl'
    curl.write_text('#!/bin/sh\nsleep .2\nexit 0\n')
    curl.chmod(0o755)
    p = start({'internal_ollama_port':free_port()}, SERVE_FAIL='1')
    output = p.communicate(timeout=4)[0]
    assert p.returncode != 0 and 'Managed Ollama exited' in output
    assert not any('backend' in r for r in records())


@pytest.mark.parametrize('victim', ['ollama', 'backend'])
def test_child_failure_stops_and_reaps_sibling(runtime, victim):
    start, records, _, bin_dir = runtime
    install_ollama(bin_dir)
    p = start({'llm_provider':'openai', 'enable_rag':True, 'internal_ollama_port':free_port()})
    wait_for(lambda: any('backend' in r for r in records()))
    serve = next(r['pid'] for r in records() if r.get('command') == ['serve'])
    backend = next(r['backend'] for r in records() if 'backend' in r)
    os.kill(serve if victim == 'ollama' else backend, signal.SIGKILL)
    output = p.communicate(timeout=4)[0]
    assert p.returncode != 0 and 'ERROR:' in output
    for pid in (serve, backend):
        with pytest.raises(ProcessLookupError): os.kill(pid, 0)


@pytest.mark.parametrize('host,mode', [('http://localhost:11434','external'), ('http://localhost:12345','auto'), ('https://ollama.example/proxy','auto'), ('http://127.0.0.1.example:11434','auto')])
def test_external_target_is_preserved_without_starting_server(runtime, host, mode):
    start, records, _, bin_dir = runtime
    install_ollama(bin_dir)
    p = start({'ollama_host':host, 'ollama_mode':mode})
    wait_for(lambda: records())
    assert records() == [{'backend':records()[0]['backend'], 'host':host}]
    p.terminate()
    assert p.wait(timeout=4) == 0


def test_rag_client_binds_effective_target_at_construction(monkeypatch, tmp_path):
    from unittest.mock import Mock
    import rag_manager
    client = Mock()
    client.embeddings.return_value = {'embedding':[0.1]}
    factory = Mock(return_value=client)
    monkeypatch.setenv('OLLAMA_HOST', 'http://127.0.0.1:12345')
    monkeypatch.setattr(rag_manager.ollama, 'Client', factory)
    monkeypatch.setattr(rag_manager.chromadb, 'PersistentClient', Mock())
    rag = rag_manager.RagManager(persist_dir=str(tmp_path / 'chroma'))
    factory.assert_called_once_with(host='http://127.0.0.1:12345')
    assert rag._generate_embedding('test') == [0.1]
    client.embeddings.assert_called_once_with(model='nomic-embed-text', prompt='test')


def test_backend_exit_code_is_propagated(runtime):
    start, records, _, _ = runtime
    p = start(BACKEND_FAIL='1')
    assert p.wait(timeout=4) == 7


def test_cloud_false_starts_only_backend_and_drains_on_term(runtime):
    start, records, _, _ = runtime
    p = start()
    wait_for(lambda: records() or p.poll() is not None)
    assert records(), p.communicate(timeout=2)[0] if p.poll() is not None else "backend not started"
    pid = records()[0]['backend']
    p.terminate()
    assert p.wait(timeout=4) == 0
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
