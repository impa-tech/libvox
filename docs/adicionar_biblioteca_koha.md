# Adicionar uma biblioteca Koha ao LibVox

Do zero até a MARC.IA respondendo sobre o acervo: habilitar a API do Koha, criar
a credencial, cadastrar a biblioteca no `/admin`, criar a conta do bibliotecário
e configurar tudo no painel dele.

Para levantar um Koha de testes antes disso, veja
[install_koha.md](install_koha.md).

## Quem faz o quê

O caminho passa por três mãos, e trocar a ordem trava o passo seguinte.

| # | Onde | Quem | O que |
| :-- | :--- | :--- | :--- |
| 1 | Koha (intranet) | Quem administra o Koha da biblioteca | Habilita a API e cria a chave somente-leitura |
| 2 | `http://localhost:8090/admin` | Operador LibVox | Cadastra a biblioteca e a conta do bibliotecário |
| 3 | `http://localhost:8090/app` | Bibliotecário | Conecta o catálogo, importa o acervo, publica o widget |
| 4 | `http://localhost:8090/admin` | Operador LibVox | Muda a situação para **Ativa** |

Troque `localhost:8090` pelo endereço onde o portal está publicado.

---

## 0. Antes de começar

* **O LibVox no ar.** `./deploy/up.sh` na raiz do repositório; ao final há cinco
  contêineres de pé. Ver [deploy/README.md](../deploy/README.md).
* **Uma conta de operador.** Numa instalação nova ela vem do seeder:

  ```bash
  cd libvox-portal && ./portal.sh seed
  ```

  Cria os dois papéis e o primeiro operador — `operator@libvox.local` /
  `password`, ou o que estiver em `LIBVOX_OPERATOR_EMAIL` e
  `LIBVOX_OPERATOR_PASSWORD` no `.env` do portal. **Troque a senha antes de o
  painel estar acessível a mais alguém.**
* **O Koha alcançável a partir dos contêineres do LibVox** — não do seu
  navegador. É a diferença que mais custa tempo aqui; a seção 2 trata dela.

---

## 1. No Koha: habilitar a API e criar a credencial

> Num Koha de testes (`ktd`) o banco volta zerado a cada `ktd up`. Esta seção
> inteira precisa ser refeita a cada nova instância.

### 1.1 Ligar o fluxo de credenciais de cliente

Administração → Preferências do sistema, e ponha
**`RESTOAuth2ClientCredentials`** em *Enable*. Atalho num Koha local:

```
http://localhost:8081/cgi-bin/koha/admin/preferences.pl?tab=&op=search&searchfield=RESTOAuth2ClientCredentials
```

### 1.2 O usuário da API

Crie (ou escolha) um *patron* que tenha **as duas coisas**:

1. um **`userid`** — nome de usuário para login, e
2. a permissão **`catalogue`** ("Staff access, allows viewing of catalogue in
   staff interface"), em *More → Set permissions*.

As duas, ou não funciona. Um patron com a flag `catalogue` mas **sem `userid`**
recebe `403` em toda chamada, porque o Koha resolve permissão por nome de
usuário — e a mensagem de erro cita justamente a permissão que você já concedeu:

```json
{"error": "Authorization failure. Missing required permission(s).",
 "required_permissions": {"catalogue": "1"}}
```

**Conceda `catalogue` e mais nada.** Ela cobre a leitura dos dados
bibliográficos e de exemplares — tudo de que o LibVox precisa — sem circulação,
sem edição e sem dados de leitores. O LibVox nunca escreve no ILS. Não use
`superlibrarian` para fazer um erro sumir: funciona, e encerra silenciosamente a
garantia de que a integração é somente leitura.

### 1.3 A chave

No perfil desse usuário: *More → Manage API keys → New API key*.

* **Description:** algo que diga a quem pertence, por exemplo `libvox`.
* Anote o **Client ID** e o **Secret**. O secret aparece uma vez.

### 1.4 Conferir por fora, antes de sair do Koha

Use [`tools/koha.sh`](../tools/koha.sh), que já faz o `client_credentials`, o
`Bearer` e a formatação da resposta. Precisa de `jq` instalado.

```bash
export KOHA_URL="http://localhost:8081"
export CLIENT_ID="..."
export CLIENT_SECRET="..."

./tools/koha.sh auth              # troca a chave por um token e mostra o token
./tools/koha.sh list              # lista os 5 primeiros registros do acervo
./tools/koha.sh search "xadrez"   # busca por título
./tools/koha.sh get_biblio 1      # o MARC-in-JSON de um registro
./tools/koha.sh availability 1    # os exemplares físicos de um registro
```

As três variáveis já vêm com um valor padrão apontando para o Koha local do
`ktd`; os `export` acima são o que troca isso por outra instância. Sem nenhum
argumento, o script imprime a própria ajuda.

**Rodar `auth` e `list` é o teste que importa**, e são dois casos diferentes:
`auth` prova a chave, `list` prova a permissão. Uma chave perfeita com o patron
sem a flag `catalogue` passa no primeiro e falha no segundo.

O valor do script está no que ele diz quando falha — as mesmas três situações
que o painel distingue depois:

| Ele diz | O que é |
| :--- | :--- |
| Emitiu um token e depois recusou esse mesmo token (401) | A credencial está certa: o `/oauth/token` a validou para emitir. É cadastro expirado, usuário da API sem `userid`, ou um Koha em CGI. |
| Autenticou, mas falta permissão (403) | A flag `catalogue` (seção 1.2). |
| Respondeu com HTML, não com a API do Koha | Um proxy anti-robô na frente do site — Anubis, Cloudflare —, comum em servidores públicos de demonstração. A credencial nem chegou a ser avaliada; reconferir o secret aqui é procurar no lugar errado. |

Se `auth` e `list` responderem, a credencial está pronta. O que ainda pode
falhar depois disso é endereço — que é a seção seguinte.

> As mesmas duas chamadas em `curl` cru, para quando não houver o repositório à
> mão, estão em [install_koha.md](install_koha.md#6-testando-a-api-via-terminal-curl).

---

## 2. Qual endereço informar ao LibVox

O endereço é usado **de dentro dos contêineres**, então `127.0.0.1` e
`localhost` apontam para o próprio contêiner, não para a sua máquina.

| Onde o Koha roda | O que digitar no painel |
| :--- | :--- |
| Na mesma máquina do LibVox, fora de contêiner | `http://host.docker.internal:8081` |
| Em outro servidor | A URL pública real, `https://koha.suabiblioteca.org` |
| Na mesma máquina, mas **`localhost`** | ❌ nunca — é o contêiner |

`host.docker.internal` está mapeado em `libvox-core`, `libvox-sync` e
`libvox-driver-koha`, inclusive no Linux, então funciona sem mais nada.

Pré-voo opcional, exercitando autenticação, permissão e normalização de uma vez
— em `libvox-driver-koha`, com `.env` preenchido a partir de `.env.example`:

```bash
cd libvox-driver-koha
docker compose run --rm driver get-biblio --id 1
```

Isso roda no mesmo tipo de contêiner que o LibVox usa, então prova o endereço e
a credencial juntos. Quem prova de verdade, porém, é o botão **Testar conexão**
do painel (seção 5) — este passo só antecipa o diagnóstico.

---

## 3. No `/admin`: cadastrar a biblioteca

Entre como operador em `http://localhost:8090/admin` → **Bibliotecas** → *Nova*.

| Campo | O que pôr |
| :--- | :--- |
| **Identificador** | `lib-` + letras minúsculas, números e hífens: `lib-central`, `lib-ibict`. **Não pode ser alterado depois** — o banco recusa a renomeação com um gatilho, porque os registros já indexados carregam essa string em outro banco. |
| **Nome** | O nome que aparece nos painéis. |
| **Situação** | Deixe **Em configuração** até a importação funcionar. Só **Ativa** faz um canal responder a leitores. |
| **Idioma** | `pt-BR` ou `en`. É o padrão das respostas da assistente. |
| **Contato** | E-mail de quem responde por essa biblioteca. |

Salvar cria mais do que a linha da biblioteca: também as configurações do widget
e um **canal web**, com a chave já gerada e **desligado**. Ele nasce assim
porque ninguém aqui sabe onde o widget vai morar — quem sabe é a bibliotecária,
e é ela que registra o endereço na seção 7. Enquanto não houver uma origem
registrada, o banco não deixa ligar o canal: um widget sem origem é embutível em
qualquer página da internet.

O que **não** é criado aqui é o índice: a linha da biblioteca no Discovery Index
nasce sozinha na primeira importação. Não há nada a provisionar.

---

## 4. No `/admin`: criar a conta do bibliotecário

**Contas → Nova.**

| Campo | O que pôr |
| :--- | :--- |
| **Nome**, **E-mail** | Da pessoa. O e-mail é o login. |
| **Papel** | **Bibliotecário**. (*Operador LibVox* é quem cadastra bibliotecas e não pertence a nenhuma.) |
| **Biblioteca** | Obrigatória para bibliotecário: aparece assim que o papel é escolhido. |
| **Senha** | Na criação é obrigatória. Na edição, em branco mantém a atual. |

Uma conta tem **um** papel. Um bibliotecário sem biblioteca é recusado no painel
`/app` na hora do login — sem biblioteca não há a que vincular a sessão, e uma
sessão sem vínculo lê todas as tabelas como vazias.

---

## 5. No `/app`: conectar o catálogo

Saia e entre como o bibliotecário em `http://localhost:8090/app` →
**Conexão com o catálogo**.

| Campo | O que pôr |
| :--- | :--- |
| **Sistema** | Koha. |
| **Endereço** | O da seção 2. |
| **Client ID** | Da seção 1.3. É um identificador, não um segredo. |
| **Client secret** | Da seção 1.3. Criptografado antes de ser gravado e **nunca mais exibido**. Em branco numa edição posterior quer dizer "mantenha o que está guardado". |

**Salvar**, depois **Testar conexão**. O teste vai ao servidor da biblioteca e
distingue o que "não funcionou" esconde:

| O que o teste diz | O que fazer |
| :--- | :--- |
| Funcionou | Seguir para a importação. |
| Credenciais aceitas, **nenhum registro** | Quase sempre é o usuário da API sem a flag `catalogue`. Não ignore: um acervo que parece vazio faz a assistente dizer que a biblioteca não tem nada. |
| Credenciais recusadas | Client ID/secret errados, ou o usuário da API inativo. |
| Emitiu um acesso e depois o recusou | Uma chave expirada, ou um usuário da API sem `userid`. Num Koha antigo servido por CGI é o normal — cada requisição é um processo novo e o acesso emitido não sobrevive até a seguinte — mas o Koha Testing Docker já vem sob Plack, então não é esse o caso aqui. |
| Faltam permissões | A flag `catalogue` (seção 1.2). |
| Não consegui alcançar o endereço | Endereço errado — reveja a seção 2 — ou firewall. |
| Respondeu, mas não é a API do Koha | Um proxy na frente do site interceptando: página de login, bloqueio anti-robô. A credencial nem chegou a ser avaliada. |

Salvar a conexão é o que libera o botão de importação.

---

## 6. No `/app`: a primeira importação

**Acervo → Atualizar índice.** A confirmação diz o que vai acontecer: a
biblioteca inteira é lida do ILS, são centenas de requisições ao servidor dela e
leva alguns minutos. O botão fica desabilitado quando não há conexão
configurada, quando já existe um pedido pendente ou quando uma importação está
em curso.

Ao confirmar, a tela de importação abre e acompanha sozinha:

| O que aparece | O que é |
| :--- | :--- |
| *Pedido registrado, aguardando* | O worker ainda não pegou. Some em segundos. |
| *O pedido ainda não foi pego* | Passou de dez segundos: ou o worker está fora do ar, ou está importando o acervo de outra biblioteca. |
| *N registros importados até agora* | Em curso. A porcentagem só existe a partir da segunda importação — a primeira não tem com o que comparar. |
| *A importação não terminou* | Falhou; o erro do servidor aparece na tela e volta a aparecer na confirmação da próxima tentativa. |
| *Importação concluída* | Pronto: a assistente já encontra esses registros. |

Confira na lista do **Acervo**, buscando como um leitor buscaria. O que está
nessa lista é exatamente o que a assistente consegue encontrar.

> A importação é o único caminho que gera carga no servidor da biblioteca. A
> busca do leitor nunca sai do índice do LibVox.

---

## 7. No `/app`: a assistente e o canal

**Assistente** — nome, tom, mensagem de abertura, idioma padrão e cor de
destaque, com prévia ao vivo. No fim da página fica a seção **Instalação**, que
é onde o widget é publicado, nesta ordem:

1. **Onde o widget pode ser embutido** — a página do OPAC, só esquema e host
   (`https://biblioteca.exemplo.br`). Digite como quiser: `HTTPS://`, porta 443,
   caminho depois da barra; o que fica gravado é a origem normalizada.
2. **A linha para colar** no template do OPAC, antes de `</body>`, já com a
   chave da biblioteca:

   ```html
   <script src="http://SEU-HOST:8092/widget.js" data-key="..."></script>
   ```

3. **A chave que liga o canal.** Ela não vira sem o passo 1 — o banco recusa, e
   é a origem que torna a chave confiável, não a chave em si.

**Canais** — a mesma origem e a mesma chave, na tela que lista todos os canais.
É a mesma coluna da mesma linha, então as duas telas não têm como discordar;
esta é onde ficam as bibliotecas com mais de uma página e os canais futuros.

> Em HTTP puro o navegador de um OPAC servido por HTTPS recusa carregar o
> script, e nada aparece na página sem erro visível. Um proxy com TLS na frente
> das portas públicas é o primeiro item para qualquer coisa além de teste.

---

## 8. De volta ao `/admin`: ativar

Operador → **Bibliotecas** → a biblioteca → **Situação: Ativa**.

É a única situação sob a qual um canal responde a leitores. Enquanto estiver *Em
configuração*, tudo funciona nos painéis e nada responde no widget.

---

## 9. Conferindo

### 9.1 Pelo terminal

Da raiz do repositório, como operador da instalação:

```bash
cd libvox-core
./core.sh check lib-central chess     # prova as quatro rotas do Core
./core.sh ask   lib-central "vocês têm alguma coisa sobre xadrez?"
```

`check` e qualquer pergunta que chegue a disponibilidade fazem algumas
requisições ao vivo ao servidor da biblioteca — poucas, mas reais.

### 9.2 Pelo navegador, num OPAC de mentira

Antes de mexer no OPAC de verdade, dá para servir localmente uma página que
finge ser o catálogo da biblioteca, com o widget real dentro dela:
[`libvox-core/demo/`](../libvox-core/demo/README.md).

```bash
cd libvox-core
./core.sh widget lib-central          # sobe a página em http://127.0.0.1:8093
```

É o mesmo que `./demo/serve.py --tenant lib-central`, com os padrões
preenchidos. Precisa estar de pé o Core (`./core.sh`, portas 8091 e 8092), a
camada MCP em modo HTTP (`cd libvox-mcp && ./mcp.sh`) e a biblioteca com um
**canal web** — o que a seção 7 configurou.

**Por que um servidor e não abrir o arquivo:** `file://` manda `Origin: null`,
o gateway recusa a conexão, e a recusa parece um widget quebrado quando é
exatamente a verificação de origem funcionando.

Ao subir, o script imprime a origem que está servindo e diz se a biblioteca já
a permite:

```
  http://127.0.0.1:8093/

  widget   http://127.0.0.1:8092/widget.js
  key      wk_f4mXL8eqbdJ2vtAhpTmSfuOtgzwltKsU
  library  lib-central

  Add this origin to the library's web channel,
  or the handshake is refused:
      http://127.0.0.1:8093
```

Cole essa string em **Assistente → Instalação → Onde o widget pode ser
embutido** (ou em **Canais → Origens permitidas**, que é o mesmo campo) e a
mensagem muda para
*"This origin is already allowed"*. **A comparação é literal:**
`http://127.0.0.1:8093` e `http://localhost:8093` são origens diferentes para o
navegador — registre a que está na barra de endereços.

Abra a página, clique no botão do canto e pergunte alguma coisa sobre o acervo.
A ordem esperada é: a saudação da biblioteca, na cor dela; **consultando o
catálogo…**, que é uma busca de verdade acontecendo e não uma animação; e a
resposta. Uma segunda pergunta do tipo *"o segundo está disponível?"* deve ir
direto para **vendo se está na estante…**, sem buscar de novo — a conversa fica
guardada, e é isso que faz a segunda pergunta saber de que livro a primeira
falava.

Quando não funciona:

| Sintoma | Quase sempre é |
| :--- | :--- |
| O botão aparece e clicar não faz nada | Conexão recusada, e em nove de dez vezes é a origem. Toda recusa é idêntica vista de fora, de propósito — o motivo está em `./core.sh logs`. |
| Não aparece botão nenhum | O `widget.js` não carregou. Confira se `http://127.0.0.1:8092/widget.js` responde. |
| *"Não consegui responder agora."* | O gateway falou com a assistente e ela não terminou: sem modelo configurado, ou a camada MCP fora do ar. |

`--key wk_...` no lugar de `--tenant` dispensa banco e Docker, o que permite
apontar essa página para um Core hospedado por outra pessoa.

### 9.3 As conversas

No `/admin`, em **Conversas**, escolha a biblioteca para ler as transcrições. Os
filtros *busca vazia* e *falha de ferramenta* são onde mora a resposta
confiantemente errada.

---

## 10. Depois

* **Reimportar:** *Acervo → Atualizar índice*, quando quiser. O acervo anterior
  continua respondendo até a nova importação terminar.
* **Trocar o segredo:** *Conexão com o catálogo*, preencher só o campo do
  secret. Deixá-lo em branco mantém o guardado.
* **Koha de testes recriado:** `ktd up` zera o banco, então a preferência, o
  usuário da API e a chave precisam ser refeitos (seção 1) e o secret regravado
  no painel.
* **Desligar sem apagar:** *Situação → Suspensa*. Nada é removido.

---

## Checklist

- [ ] `RESTOAuth2ClientCredentials` habilitada no Koha
- [ ] Usuário da API com `userid` **e** permissão `catalogue`, e nada além dela
- [ ] Client ID e secret anotados
- [ ] Endereço alcançável de dentro dos contêineres
- [ ] Biblioteca cadastrada no `/admin`, com identificador `lib-…`
- [ ] Conta de bibliotecário criada, com papel e biblioteca
- [ ] Conexão salva e **Testar conexão** dizendo que funcionou
- [ ] Primeira importação concluída, e o Acervo listando registros
- [ ] Widget configurado, com o endereço do OPAC registrado e o canal ligado
- [ ] Situação da biblioteca em **Ativa**
- [ ] Widget respondendo no OPAC de mentira (`./core.sh widget lib-…`)
