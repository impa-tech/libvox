# Requisitos de sistema

O que é preciso para rodar o LibVox, medido numa instalação real em setembro de
2026 — não estimado a partir de recomendações de fornecedor.

**A primeira coisa a entender é que são duas máquinas, e elas podem ser a
mesma.** O LibVox em si — cinco contêineres e um PostgreSQL — é leve e **não
precisa de GPU nenhuma**. O que precisa de GPU é o servidor de modelos, que o
LibVox procura por HTTP e que pode estar em qualquer lugar — a chamada sai
sempre daqui para lá, nunca o contrário. Na instalação em que estes números
foram medidos ele está noutro host inteiramente.

| | precisa de GPU? | |
| :--- | :--- | :--- |
| **LibVox** — os cinco contêineres e o banco | não | qualquer VPS modesto |
| **Servidor de modelos** — o LLM e o de embedding | sim, ou é muito lento | ou um serviço de terceiros |

---

## 1. A máquina do LibVox

### Medido, com tudo no ar

| Contêiner | Memória em uso |
| :--- | ---: |
| `libvox-db-postgres-1` | 103 MB |
| `libvox-portal-portal-1` | 86 MB |
| `libvox-core-core-1` | 71 MB |
| `libvox-sync-worker-1` | 43 MB |
| `libvox-mcp-mcp-1` | 42 MB |
| **total** | **345 MB** |

Isso é com dois acervos indexados, 894 registros, o worker escutando e o portal
servindo. Não é ocioso — é o estado normal de operação.

Num host atrás do proxy há um sexto, o `libvox-proxy-caddy-1`, que não entra na
medição acima porque a máquina medida serve as portas direto. Caddy é pequeno
— dezenas de megabytes.

> **Esses nomes são garantidos, não derivados.** O compose monta o nome como
> `<projeto>-<serviço>-<índice>` e tiraria o projeto do nome da pasta, o que
> deixaria todo `docker exec` desta página quebrado para quem clonou em outro
> lugar. Cada arquivo de compose fixa o seu com `name:`, então os cinco acima
> valem em qualquer máquina.

| | |
| :--- | :--- |
| **CPU** | 2 núcleos bastam. A máquina medida tem 8 e nenhum passa perto de saturar; o trabalho pesado é do banco durante uma importação. |
| **RAM** | **2 GB** para rodar, **4 GB** para trabalhar com folga. Os 345 MB acima mais o cache do PostgreSQL, que cresce com o acervo. |
| **Disco** | **4 GB** para as imagens e o sistema, mais o banco (abaixo). |

### As imagens

| | |
| :--- | ---: |
| `pgvector/pgvector:pg17` | 443 MB |
| `libvox-portal:dev` | 782 MB |
| `libvox-core:dev` | 237 MB |
| `libvox-mcp:dev` | 188 MB |
| `libvox-sync:dev` | 163 MB |
| **total** | **1,8 GB** |

O portal é a maior porque carrega PHP, Composer e o bundle de CSS já construído.

### O banco, e como estimar o seu

Medido com **894 registros** de duas bibliotecas, com vetores:

```
discovery_biblio       tabela 5,8 MB   índices 7,9 MB   total 13 MB
discovery_vocabulary   tabela 528 kB   índices 912 kB   total 1,4 MB
```

Os índices pesam mais que os dados, e vale ver por quê — são quatro, e um deles
é quase tudo:

| | total | por registro |
| :--- | ---: | ---: |
| a tabela | 5,8 MB | 6,5 kB |
| `discovery_biblio_embedding_idx` (HNSW) | 7,0 MB | **8,1 kB** |
| `discovery_biblio_search_idx` (GIN) | 704 kB | 0,8 kB |
| chave primária e o btree incremental | 144 kB | 0,2 kB |
| **soma** | **13 MB** | **≈ 15 kB** |

**O HNSW sozinho é mais da metade.** São os 4 kB do vetor mais os enlaces do
grafo, e é a parte que quer estar em memória — um índice ANN que vive em disco
responde, e responde devagar.

| Acervo | Discovery Index |
| ---: | ---: |
| 10 mil registros | ~150 MB |
| **50 mil** | **~770 MB** |
| 100 mil | ~1,5 GB |
| 500 mil | ~7,5 GB |

Some os outros cinco bancos — registro de bibliotecas, conversas, e uma cópia
`_test` de cada — que juntos ocupam ~50 MB numa instalação de desenvolvimento e
crescem com o volume de conversas, não com o acervo.

> **A extrapolação é linear e a realidade não é bem.** O HNSW cresce um pouco
> mais que proporcional, e acervos com muito resumo pesam mais que os medidos
> aqui (2,5% dos registros do acervo maior têm resumo). Trate como ordem de
> grandeza, não como orçamento.

### O caso concreto: 50 mil registros

A primeira biblioteca real a ser sincronizada tem cerca de **50 mil registros**.
Aplicando as medições acima:

| | |
| :--- | ---: |
| tabela | ~325 MB |
| HNSW | ~405 MB |
| GIN | ~40 MB |
| vocabulário | ~10 MB |
| **Discovery Index** | **~780 MB** |
| com WAL, inchaço e os outros bancos | **~1,5 GB de disco** |

O vocabulário não escala com os registros e sim com as **palavras distintas**,
que crescem devagar: 458 registros de um acervo deram 4.020 palavras, e cinquenta
mil dão dezenas de milhares, não milhões.

**RAM: 4 GB deixa de ser conforto e passa a ser o mínimo.** Os 780 MB do índice
querem caber no cache de páginas junto com os 345 MB dos contêineres, e a
resposta de uma busca vetorial depende disso. Com 2 GB funciona e o HNSW passa a
ler disco a cada consulta. **8 GB** é onde eu ficaria tranquilo.

**A primeira importação leva cerca de 40 minutos**, em duas metades:

| | |
| :--- | ---: |
| ler o acervo do Koha | ~22 min — medido a ~37 registros/s contra um Koha pela internet |
| calcular os embeddings | ~18 min — medido a 45 registros/s |

**As importações seguintes levam ~22 minutos**, não 40: os vetores são herdados
da geração anterior em toda linha cujo título, série e autor não mudaram, então
o segundo dia paga só a leitura do acervo.

> A velocidade de leitura é do servidor da biblioteca e da rede até ele, não
> nossa. Os 37 registros/s vieram de um Koha de demonstração pela internet
> pública; um servidor na mesma rede vai mais rápido, e um sobrecarregado vai
> mais devagar. Meça com `extract --limit` antes de prometer o número.

**E uma coisa específica deste acervo.** Uma biblioteca de matemática cataloga
majoritariamente em inglês, e o índice léxico está configurado em português —
`libvox_search` é uma configuração só, para todos os registros de todas as
bibliotecas. O efeito está medido no README da raiz: o stemmer português come o
`-is` de `Mathis` e o transforma em `math`, e `curso` colide com `curse`. Para
um acervo assim, **o modelo de embedding deixa de ser opcional** — é o braço
vetorial que atravessa o idioma, e sem ele metade das perguntas em português
não alcança a estante.

### Portas

| Porta | O quê | Exposição |
| ---: | :--- | :--- |
| 8090 | Painéis `/admin` e `/app` | pública, atrás do proxy |
| 8092 | Widget e WebSocket do leitor | pública, atrás do proxy |
| 8091 | API interna do Core | **só loopback** — ela escolhe a biblioteca pelo corpo da requisição |
| 8000 | Servidor MCP | rede interna |
| 5435 | PostgreSQL | só loopback. 5435 e não 5432, porque o Koha Testing Docker também quer a padrão |

Atrás do proxy, `LIBVOX_BIND=127.0.0.1` fecha 8090 e 8092 na interface pública —
o Caddy alcança os contêineres pelo nome.

---

## 2. A máquina do modelo

Aqui é onde a GPU importa, e onde o custo real está.

### O que o LibVox pede de um servidor de modelos

Dois endpoints compatíveis com a OpenAI, e o segundo é opcional:

| | endpoint | obrigatório |
| :--- | :--- | :--- |
| conversa | `/v1/chat/completions` com *tool calling* | sim |
| embedding | `/v1/embeddings` | não — sem ele a busca é só léxica |

Ollama, vLLM, LM Studio ou um gateway como o LiteLLM à frente deles. O LibVox
não sabe qual é.

### VRAM

Os dois modelos em uso, e o que eles pedem:

| Modelo | Papel | Pesos | Estimado em VRAM |
| :--- | :--- | ---: | ---: |
| `Qwen3.5-9B` Q6_K | conversa | ~7,3 GB | **9–10 GB** com contexto |
| `qwen3-embedding:4b` | busca semântica | ~2,5 GB (Q4) | **~3 GB** |
| | | | **~13 GB os dois** |

> **Pesos são medidos, o total é estimado.** O tamanho dos pesos sai do número
> de parâmetros e da quantização; o resto é cache de contexto, que depende de
> quanto contexto o servidor reserva. Não meço a GPU de vocês daqui.

**Recomendação:** uma placa de **16 GB** segura os dois com folga. Com **12 GB**
cabe o modelo de conversa e um contexto modesto, e o de embedding vai para outra
máquina ou para a CPU — ele é chamado uma vez por pergunta e uma vez por
registro na importação, então tolera ser lento muito melhor que o de conversa.

**Abaixo disso funciona e é lento.** O `libvox-core/README.md` registra o efeito
medido: os modelos da tabela de comparação rodavam parcialmente na CPU porque a
placa era compartilhada, e o mesmo 9B mediu 5,7 s numa execução com 13% na CPU
contra 4,8 s noutra, sem o modelo mudar.

### Tamanho do modelo de conversa, medido

Não é gosto — está medido no `libvox-core/README.md`, sobre a mesma pergunta:

| | acha e confere disponibilidade | buscas por resposta |
| :--- | ---: | ---: |
| `Qwen3.5-9B` Q6_K | **8 / 8** | 1 |
| `qwen3.6:35b-a3b` | **8 / 8** | 1 |
| `qwen3.8:27b` | **8 / 8** | 1 – 3 |
| `hermes3:8b` | **0 / 8** | 1 |

**Um 9B bom vence um 35B**, e um 8B ruim não responde nada. Toda pergunta aqui é
uma cadeia — buscar, depois conferir a estante — e é isso que separa os dois: o
`hermes3:8b` responde rápido e sem chamar ferramenta nenhuma, o que parece
funcionar até alguém ler a resposta.

A quantização importa para a afirmação: **Q6_K**, seis bits, onde o
arredondamento é pequeno o bastante para o modelo se comportar como o original.
Um 9B em três bits mediria a compressão, não o modelo.

### Desempenho, medido nesta instalação

| | |
| :--- | ---: |
| pergunta com resposta no acervo | 6,3 s (mediana de 8) |
| pergunta sem resposta | 30,8 s (mediana de 8) |
| embedding de uma consulta | ~0,2 s com conexão quente |
| embedding na importação | ~45 registros/s → 3,2 min para 8.774 |

> Os 30,8 s da segunda linha são um defeito conhecido e não uma propriedade do
> hardware: a re-tentativa alarga a pergunta, o braço vetorial responde a
> pergunta alargada, e o modelo gasta o tempo lendo e rejeitando registros.

---

## 3. Software

### Para rodar

| | |
| :--- | :--- |
| Docker e Docker Compose | é tudo. Os cinco módulos são contêineres. |
| PostgreSQL | **não instale** — vem no `/libvox-db`, na imagem `pgvector/pgvector:pg17` |

A imagem do banco **não é a `postgres` padrão**: a extensão `vector` é um módulo
em C e não se instala por SQL. Um diretório de dados escrito sob a imagem Alpine
anterior precisa de `REINDEX` depois da troca — musl e glibc ordenam texto
diferente, e todo índice btree sobre texto passa a discordar da collation que o
lê.

### Para desenvolver

| | |
| :--- | :--- |
| Python | **3.10+**, um ambiente conda por módulo (`environment.yml` em cada) |
| PHP | **8.5** com Composer, para o portal |
| Node | 22, só para o bundle de CSS — e ele é construído dentro da imagem |
| `jq` | usado pelos scripts de shell |

---

## 4. O que **não** é requisito

Vale dizer, porque cada um destes já foi considerado e recusado com um motivo
registrado:

- **GPU na máquina do LibVox.** Nenhum dos cinco contêineres faz inferência.
- **Redis, ou qualquer cache.** O único trabalho genuinamente efêmero seria um
  cache curto sobre `check_availability`, e ele não existe ainda.
- **Um motor de busca dedicado.** Medido contra o Meilisearch e recusado: ele
  entrega ~50–60% de recall de flexão em qualquer idioma contra ~100% do
  stemmer no idioma configurado. O README da raiz tem a tabela e o ponto de
  equilíbrio.
- **Fila de mensagens.** O outbox é uma tabela no PostgreSQL com `LISTEN` e uma
  varredura de cinco segundos por baixo.
- **Modelo de embedding.** Sem ele o índice é construído sem vetores e a busca é
  respondida só pelo braço léxico, que é o que o sistema fez durante um ano.
- **Qualquer coisa do lado do ILS.** O servidor da biblioteca é dela, e o LibVox
  só o lê — por uma chave de leitura, e nunca escrevendo. O que ele precisa para
  rodar, e o quanto de máquina pede, não é assunto desta página. Ligar um acervo
  novo é a [`adicionar_biblioteca_koha.md`](adicionar_biblioteca_koha.md).

---

## 5. Conferindo na sua máquina

```bash
# memória de cada contêiner, com tudo no ar
docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}' | grep libvox

# o que o banco ocupa, por tabela
docker exec libvox-db-postgres-1 psql -U libvox -d libvox_index -c "
select relname,
       pg_size_pretty(pg_total_relation_size(c.oid)) as total
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public' and c.relkind = 'r'
 order by pg_total_relation_size(c.oid) desc;"

# e se o servidor de modelos aguenta: 8 perguntas com resposta e 8 sem
cd libvox-core && ./core.sh bench lib-local 8
```

O `bench` é o teste que importa. Ele atravessa as quatro imagens e os três
bancos, e a coluna **buscas por resposta** é sobre o modelo e não sobre a
máquina — um modelo que busca três vezes onde outro busca uma vez não fica bom
com uma placa maior.

E antes de qualquer coisa, `./deploy/up.sh check` diz o que está configurado e o
que falta, incluindo se a busca vetorial está ligada, desligada ou pela metade.

---

## Resumo

Se for ler uma coisa só desta página, que seja esta tabela.

| | mínimo | confortável |
| :--- | :--- | :--- |
| **Máquina do LibVox** | 2 núcleos · 2 GB RAM · 50 GB disco **+ o banco** | 4 núcleos · 4 GB RAM · SSD |
| **Máquina do modelo** | 12 GB VRAM (só o de conversa) | **16 GB VRAM** (os dois modelos) |
| **Software** | Docker e Docker Compose | — |

**O banco é o que cresce**, e é o único número que depende da biblioteca: conte
**15 kB por registro** — 1,5 GB para um acervo de cem mil.

E para o primeiro acervo real, de **50 mil registros**, a linha de cima muda:

| | |
| :--- | :--- |
| **Máquina do LibVox** | 4 núcleos · **8 GB RAM**  · **50 GB de disco** |
| **Máquina do modelo** | GPU 32GB |

**Três coisas que costumam ser dimensionadas erradas:**

1. **GPU na máquina do LibVox não serve para nada.** Nenhum dos cinco
   contêineres faz inferência. Se o orçamento é um só, ele vai inteiro para o
   servidor de modelos.
2. **Placa maior não conserta modelo ruim.** Medido: um 9B bom responde 8 de 8 e
   um 8B ruim responde 0 de 8, e a diferença é quantas vezes cada um chama uma
   ferramenta — comportamento do modelo, não do hardware.
3. **Nada disso é obrigatório de uma vez.** Sem modelo de embedding a busca é só
   léxica e o sistema funciona; sem GPU alguma, um endpoint compatível com a
   OpenAI de terceiros também serve. O LibVox não sabe quem está do outro lado.
