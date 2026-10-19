# Bundled Ollama runtime (0.13.12)

Ollama is started and supervised by `ai-orchestrator/run.sh`, not systemd. Generation can use OpenAI while local Ollama supplies RAG embeddings.

## Configuration

```yaml
ollama_mode: auto
ollama_host: http://localhost:11434
internal_ollama_port: 11435
```

- `auto`: the legacy default localhost/127.0.0.1:11434 URL selects the managed internal server. Other URLs select external mode.
- `internal`: use the managed loopback server on `internal_ollama_port`, ignoring `ollama_host`.
- `external`: use `ollama_host` unchanged; never launch, stop or reconfigure that server. Use this mode to intentionally connect to an existing localhost:11434 service.
- The managed server only starts when generation uses Ollama or `enable_rag` is true. Cloud generation with RAG disabled does not start it.

## Owned process and storage

The managed server receives these settings before startup:

```text
HOME=/data/ollama/home
OLLAMA_MODELS=/data/ollama/models
OLLAMA_KV_CACHE_TYPE=f16
OLLAMA_HOST=http://127.0.0.1:11435
```

The f16 cache avoids the quantized V-cache / Flash Attention requirement. This is separate from the enable_gpu option; external servers are not reconfigured. Disabling enable_gpu retains the existing GPU-option behavior and is not a new hard CPU-only enforcement switch.

The loopback port is checked before launch. Occupied ports are refused rather than reusing or killing another host-network listener. Readiness probes the effective endpoint and checks that the owned process is alive. The backend and managed server are supervised together: an unexpected child exit terminates the sibling and exits nonzero. Container restart policy may then recover the service; the script does not infinitely restart failed children. Shutdown signals drain and reap owned processes.

RAG creates its Ollama client with the effective OLLAMA_HOST, including embedding-model auto-pulls, rather than retaining a module-level client bound to the old port.

## Upgrade and model downloads

No existing server on port 11434 is stopped or altered. Models listed by that server are not automatically copied or deleted. The managed server has a new persistent model directory, so its required models may need downloading again. RAG automatically pulls its embedding model on the existing missing-model path. Retain backups of older model directories and Chroma data; no data migration or deletion is performed by this change.

For a manual test in the add-on terminal, explicitly target the managed server (adjust the port if configured differently):

```bash
OLLAMA_HOST=http://127.0.0.1:11435 ollama run nomic-embed-text:latest test
```

An interactive terminal does not inherit the entrypoint's exported environment. Running the CLI without this override can still contact the unrelated default port 11434.

## Verification scope

Fourteen local fixture tests exercised real entrypoint execution, owned-child environment, custom port, occupied-port refusal, readiness failure, process cleanup, external mode and explicit RAG client routing. No live model inference or full container image build was performed. The full test suite was not rerun for this follow-up at the user's request.
