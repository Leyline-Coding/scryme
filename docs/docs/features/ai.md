# AI Assistant

scryme can connect to **any OpenAI-API-compatible endpoint** — a local model runner like
[Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai), or a hosted provider like OpenAI,
Anthropic, Google, or Perplexity — to power a set of **grounded** deck- and card-building helpers.

Everything here is **opt-in and off by default**. Until you configure an endpoint, the AI panels
simply don't appear. Your collection is never sent anywhere unless you turn this on.

!!! info "Grounded, not guessing"
    scryme doesn't ask a model to invent card names. Every suggestion is **validated against your
    local card database** (and dropped if it doesn't exist, isn't in your commander's color
    identity, or isn't format-legal), and rules answers are grounded in the card's oracle text,
    its Scryfall rulings, and the **Comprehensive Rules**. This keeps hallucinations out of your
    decklists.

## What you get

Once configured, these appear across the app:

| Feature | Where | What it does |
| --- | --- | --- |
| **Deck analysis** | a deck page → *Analyze* | Grades a deck against Commander ratios (lands / ramp / draw / removal / win-cons / synergy) and flags gaps. |
| **Upgrade planner** | a deck page → *Upgrade* | Suggests cards to buy to improve a deck, within a budget — validated, in-identity, and priced. |
| **Coaching chat** | a deck page → *Chat* | A conversation about the specific deck, grounded in its cards, colors, and format. |
| **Suggest from collection** | a deck page → *Suggest* | Recommends cards **you already own** that fit the deck. |
| **Build from a prompt** | Decks tab → *Build from collection* | Turn a plain-English request ("aggressive red goblins") into a starting decklist from owned cards. |
| **Commander finder** | `/ai/commanders` | Ranks the legendary creatures you own by how buildable a deck around them is from your collection. |
| **Natural-language search** | search bar → *Ask in plain English* | Converts "blue instants that counter spells" into `c:u t:instant o:counter o:spell`. |
| **Card rules Q&A** | a card page → *Ask a rules question* | Answers interaction questions, grounded in the card's oracle text, rulings, and the Comprehensive Rules. |
| **Similar cards** | a card page | Finds semantically similar cards you own (needs the embedding model + a one-time backfill). |

## Configuring the endpoint

Open **Settings (⚙) → AI settings** (or go to `/ai`) and fill in:

- **Base URL** — the OpenAI-compatible API root, e.g. `http://localhost:11434/v1` for Ollama.
- **API key** — required for hosted providers; optional (often blank) for local servers.
- **Chat model** — the model used for analysis/chat/suggestions.
- **Embedding model** — used for *Similar cards* and *Card rules Q&A* retrieval (optional).

Click **Test** to verify scryme can reach the endpoint, then **Save**. The API key is
**encrypted at rest**.

!!! tip "Configure by environment instead"
    Prefer to bake it into your deployment? Set these environment variables (they seed the defaults;
    the in-app Settings page overrides them):

    | Variable | Example | Purpose |
    | --- | --- | --- |
    | `SCRYME_LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible API root (empty disables AI) |
    | `SCRYME_LLM_API_KEY` | `sk-…` | API key (optional for local servers) |
    | `SCRYME_LLM_CHAT_MODEL` | `llama3.1` | Chat/completions model |
    | `SCRYME_LLM_EMBED_MODEL` | `nomic-embed-text` | Embeddings model (for similarity + rules Q&A) |

### One-time backfills (optional)

Two features rely on locally-computed embeddings, so run these once after setting an embedding
model:

```bash
# Semantic "Similar cards" for the cards you own
docker compose exec backend python -m src.cli backfill-embeddings

# Grounded rules Q&A over the Comprehensive Rules (bundled)
docker compose exec backend python -m src.cli backfill-rules
```

## Provider setup

Any OpenAI-compatible endpoint works. Below are the popular ones. For **local** models your
collection never leaves your machine; for **hosted** providers the deck/card context in each request
is sent to that provider under their terms.

### Ollama (local)

1. Install [Ollama](https://ollama.com) and pull a model plus an embedder:
   ```bash
   ollama pull llama3.1
   ollama pull nomic-embed-text
   ```
2. Ollama serves an OpenAI-compatible API at `http://localhost:11434/v1`.
3. In **Settings → AI**: Base URL `http://localhost:11434/v1`, API key *blank*, Chat model
   `llama3.1`, Embedding model `nomic-embed-text`.

!!! warning "Reaching a local server from Docker"
    If scryme runs in Docker and the model server runs on the host, `localhost` inside the
    container isn't your host. Use `http://host.docker.internal:11434/v1` (macOS/Windows), or on
    Linux add `extra_hosts: ["host.docker.internal:host-gateway"]` to the backend service — or point
    at the host's LAN IP. The **desktop app** has no such hop; `localhost` just works.

### LM Studio (local)

1. In [LM Studio](https://lmstudio.ai), download a chat model (e.g. a Gemma or Llama variant) and an
   embedding model, then start the **Local Server** (Developer tab).
2. It exposes `http://localhost:1234/v1`.
3. In **Settings → AI**: Base URL `http://localhost:1234/v1`, API key *blank*, Chat model = the
   model identifier LM Studio shows, Embedding model = your embedding model's identifier.

!!! note "Reasoning models"
    Some models (e.g. Gemma QAT) spend their token budget on hidden reasoning before answering.
    scryme already requests generous output limits and retries on empty responses, but if answers
    come back blank, pick a non-reasoning model or raise the model's max output tokens in LM Studio.

### OpenAI (ChatGPT models)

1. Create an API key at [platform.openai.com](https://platform.openai.com/api-keys).
2. In **Settings → AI**: Base URL `https://api.openai.com/v1`, API key `sk-…`, Chat model
   e.g. `gpt-4o-mini` (or another chat model), Embedding model `text-embedding-3-small`.

### Anthropic (Claude)

Anthropic offers an OpenAI-compatible endpoint.

1. Create a key at [console.anthropic.com](https://console.anthropic.com/).
2. In **Settings → AI**: Base URL `https://api.anthropic.com/v1`, API key your Anthropic key,
   Chat model e.g. `claude-sonnet-5` (or another current Claude model). Anthropic doesn't serve
   OpenAI-style embeddings — leave the embedding model blank and use a local embedder (Ollama /
   LM Studio) if you want *Similar cards* and rules Q&A.

### Google (Gemini)

Gemini exposes an OpenAI-compatible endpoint.

1. Get a key from [Google AI Studio](https://aistudio.google.com/app/apikey).
2. In **Settings → AI**: Base URL `https://generativelanguage.googleapis.com/v1beta/openai/`,
   API key your Gemini key, Chat model e.g. `gemini-2.0-flash`, Embedding model e.g.
   `text-embedding-004`.

### Perplexity

1. Get a key from your [Perplexity API settings](https://www.perplexity.ai/settings/api).
2. In **Settings → AI**: Base URL `https://api.perplexity.ai`, API key `pplx-…`, Chat model e.g.
   `sonar`. Perplexity is chat-only — leave the embedding model blank (use a local embedder for
   similarity/rules Q&A).

!!! tip "Mix and match"
    A common setup is a **hosted chat model** for quality plus a **local embedding model** (Ollama's
    `nomic-embed-text`) so *Similar cards* and rules retrieval run for free on your machine. Set the
    Base URL/key/chat model to the hosted provider and just run a local Ollama for embeddings — or
    keep everything local for full privacy.

## Privacy

- **Local endpoints** (Ollama, LM Studio): requests stay on your machine/network.
- **Hosted endpoints**: each AI request sends the relevant deck or card context (names, colors,
  the question) to that provider. No card *images* or bulk data are sent, and nothing is sent for
  features you don't invoke.
- The stored API key is encrypted at rest, and the whole subsystem is inert until you enable it.
