# Install koha test

Full documentation: [Koha Testing Docker README](https://gitlab.com/koha-community/koha-testing-docker/-/blob/main/README.md)

### 1. Configuração de Variáveis de Ambiente
Os comandos abaixo servem para configurar os caminhos do projeto e garantir que o ecossistema do Koha saiba onde encontrar os arquivos.

```bash
# Define o diretório base onde os repositórios ficarão salvos
export PROJECTS_DIR=/mnt/windows/versionado/

# Salva permanentemente o diretório base no seu arquivo de configuração do terminal
echo "export PROJECTS_DIR=$PROJECTS_DIR" >> ~/.bashrc

# Define onde o código fonte oficial do Koha ficará hospedado
echo 'export SYNC_REPO=$PROJECTS_DIR/koha' >> ~/.bashrc

# Define onde as ferramentas do Koha Testing Docker (KTD) ficarão hospedadas
echo 'export KTD_HOME=$PROJECTS_DIR/koha-testing-docker' >> ~/.bashrc

# Adiciona a pasta "bin" do KTD ao seu PATH. Isso permite que você digite "ktd" em qualquer lugar do terminal como um comando nativo
echo 'export PATH=$PATH:$KTD_HOME/bin' >> ~/.bashrc

# Pega o ID do seu usuário local do Linux e o exporta. Isso garante que os contêineres Docker rodem com a mesma permissão que você, evitando erros de permissão negada (Root vs Usuário)
echo 'export LOCAL_USER_ID=$(id -u)' >> ~/.bashrc

# Recarrega as configurações do terminal para aplicar as mudanças imediatamente nesta sessão
source ~/.bashrc
```

### 2. Download dos Repositórios
Agora que as variáveis estão configuradas, precisamos baixar as ferramentas e o código fonte.

```bash
# Baixa o repositório contendo a infraestrutura de testes (Docker Compose, scripts ktd, etc)
git clone https://gitlab.com/koha-community/koha-testing-docker.git koha-testing-docker

# Baixa o repositório com o código fonte oficial e completo do Koha ILS
git clone https://git.koha-community.org/Koha-community/Koha.git koha
```

### 3. Subindo os Contêineres
O último passo é inicializar o ambiente docker.

```bash
# Inicializa todos os contêineres (Banco de dados MariaDB, RabbitMQ, ElasticSearch, e a aplicação Koha em si). Pode demorar alguns minutos na primeira vez.
ktd down
ktd up
```

### 4. Acessar o Painel 

Após os contêineres subirem, você pode acessar as interfaces do Koha pelo navegador:

* **Painel de Administração (Intranet):** [http://localhost:8081](http://localhost:8081)
* **Catálogo Público (OPAC):** [http://localhost:8080](http://localhost:8080)

**Credenciais Padrão:**
* **Usuário:** `koha`
* **Senha:** `koha`

---

### 5. Configurando e Habilitando a API (⚠️ Importante)

> **Atenção:** Como estamos rodando um ambiente de testes efêmero, **toda vez que você subir uma nova instância (ktd up), o banco de dados virá zerado.** Você precisará habilitar a API e gerar as chaves novamente seguindo os passos abaixo.

1. Acesse as preferências do sistema pela URL abaixo:
   [http://localhost:8081/cgi-bin/koha/admin/preferences.pl?tab=&op=search&searchfield=RESTOAuth2ClientCredentials](http://localhost:8081/cgi-bin/koha/admin/preferences.pl?tab=&op=search&searchfield=RESTOAuth2ClientCredentials)
2. Mude a preferência `RESTOAuth2ClientCredentials` para **true** (Enable) e salve.
3. Crie um novo Patron (Usuário) ou acesse o perfil de um existente.
4. **Configurar Permissões (Crucial):** Acesse a seção de permissões do usuário (ex: `http://localhost:8081/cgi-bin/koha/members/member-flags.pl?member=54`). Você deve habilitar a flag **"catalogue: Staff access, allows viewing of catalogue in staff interface"**. Sem essa permissão, a API rejeitará as requisições.
5. Em seguida, vá na aba de criação de chaves de API no perfil deste usuário.
6. **Crie uma Chave de API** (exemplo):
   * **Description:** `consultar_acervo`
   * Anote o **Client ID** gerado (ex: `5c66b830-6003-4403-a303-056bfda991df`)
   * Anote o **Secret** gerado (ex: `f9b49601-6cba-4a5b-83eb-afaddf73dc9f`)

---

### 6. Testando a API via Terminal (Curl)

Depois de gerar suas credenciais, você pode testar a comunicação com a API.
> **Dica:** É recomendável exportar suas credenciais como variáveis de ambiente para facilitar os testes, seja no ambiente local ou em bibliotecas externas (como o IBICT).

```bash
# Exemplo de configuração de variáveis
export KOHA_CLIENT_ID="seu-client-id-aqui"
export KOHA_CLIENT_SECRET="seu-secret-aqui"
export KOHA_API_URL="http://localhost:8081" # Ou https://koha-demo.ibict.br/
```

#### Passo 1: Gerar o Token de Acesso (OAuth2)
Este comando troca o seu `client_id` e `client_secret` por um Token temporário.

```bash
curl -X POST "${KOHA_API_URL}/api/v1/oauth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=${KOHA_CLIENT_ID}" \
  -d "client_secret=${KOHA_CLIENT_SECRET}"
```
*(Anote o `access_token` gerado na resposta)*

#### Passo 2: Listar os Livros (Biblios)
Usando o Token obtido, você pode listar os registros bibliográficos. Substitua `SEU_TOKEN_AQUI` pelo token real gerado no Passo 1.

```bash
curl -X GET "${KOHA_API_URL}/api/v1/biblios" \
  -H "Authorization: Bearer SEU_TOKEN_AQUI" \
  -H "Accept: application/json"
```

#### Passo 3: Buscar um Livro Específico
Lembre-se de passar o JSON na URL usando a sintaxe SQL::Abstract (`like`), adicionando a flag `-g` no curl.

```bash
curl -g -X GET "${KOHA_API_URL}/api/v1/biblios?q={\"title\":{\"like\":\"%Algoritmos%\"}}" \
  -H "Authorization: Bearer SEU_TOKEN_AQUI" \
  -H "Accept: application/json"
```

---

### 7. Instâncias de Demonstração e Testes 🧪

Caso você não queira rodar o Koha localmente ou precise testar integrações reais, existem ambientes de demonstração disponíveis:

* **Koha Community Demos:** A comunidade mantém [vários servidores de demonstração](https://koha-community.org/demo/) sempre atualizados com as últimas versões do sistema.
* **IBICT (Demo Brasileira):** O Instituto Brasileiro de Informação em Ciência e Tecnologia (IBICT) disponibiliza uma [instalação demo do Koha totalmente em Português](https://www.gov.br/ibict/pt-br/central-de-conteudos/noticias/2026/maio/ibict-disponibiliza-instalacao-demo-do-software-livre-koha-em-portugues). (URL Base: `https://koha-demo.ibict.br/`).

---

### 8. Links Importantes 📚

* **Hea Koha Global:** Um [mapa e diretório completo](https://hea.koha-community.org/libraries) de bibliotecas ao redor do mundo que utilizam o Koha. Ótimo para prospectar adoção do sistema.
* **Artigo BRAPCI:** Referência bibliográfica sobre o Koha no Brasil: [https://brapci.inf.br/v/71324](https://brapci.inf.br/v/71324)