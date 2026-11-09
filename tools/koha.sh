#!/bin/bash

# ==============================================================================
# Koha API Testing Script
# ==============================================================================
# Este script facilita testes rápidos na API REST do Koha.
# Você pode sobrescrever as variáveis usando exports no terminal, exemplo:
# export CLIENT_ID="seu-id" && export CLIENT_SECRET="seu-secret" && ./koha.sh list
# ==============================================================================

# KOHA_URL=${KOHA_URL:-"https://koha-demo.ibict.br"}

# Esse funcinoa
# KOHA_URL=${KOHA_URL:-"https://koha.adminkuhn.ch:8443"}
# CLIENT_ID=${CLIENT_ID:-"0e6cc472-1342-440a-b6fb-17d762e301d1"}
# CLIENT_SECRET=${CLIENT_SECRET:-"4ecde722-f0e9-4f7d-8604-e5627e5efd87"}

KOHA_URL=${KOHA_URL:-"http://localhost:8081"}
CLIENT_ID=${CLIENT_ID:-"7bcce263-dc1c-4f3e-ad78-4eb9e2ad0920"}
CLIENT_SECRET=${CLIENT_SECRET:-"90216741-f59c-468e-824d-93f223eb4054"}


# Requer 'jq' instalado para formatar os JSONs
if ! command -v jq &> /dev/null; then
    echo "O comando 'jq' não foi encontrado. Por favor, instale-o (ex: sudo apt install jq) para formatar as respostas JSON."
    exit 1
fi

# ==============================================================================
# Funções Base
# ==============================================================================

# Função para obter e cachear o Token OAuth2
get_token() {
    RESPONSE=$(curl -s -X POST "${KOHA_URL}/api/v1/oauth/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "grant_type=client_credentials" \
        -d "client_id=${CLIENT_ID}" \
        -d "client_secret=${CLIENT_SECRET}")
    
    # Extrai o access_token usando grep e regex (evitando falhar se o jq engasgar com erro do Koha)
    TOKEN=$(echo "$RESPONSE" | grep -oP '(?<="access_token":")[^"]*')
    
    if [ -z "$TOKEN" ]; then
        # Distingue "o Koha recusou" de "quem respondeu nao foi o Koha".
        #
        # Servidores de demonstracao publicos costumam ficar atras de um proxy
        # anti-raspagem (Anubis, Cloudflare) que devolve HTML -- as vezes com
        # HTTP 200 -- para qualquer cliente que nao seja um navegador. Nesse
        # caso a requisicao nem chega na API, e a credencial e irrelevante:
        # conferir client_id e secret e procurar no lugar errado.
        if echo "$RESPONSE" | grep -qi "<html"; then
            echo "O servidor respondeu com HTML, nao com a API do Koha." >&2
            echo "Quase sempre e um proxy anti-bot na frente do site; a" >&2
            echo "credencial nao chegou a ser avaliada." >&2
            echo "" >&2
            echo "$RESPONSE" | grep -oiE "anubis|cloudflare|access denied[^<]*" | sort -u | sed 's/^/  /' >&2
        else
            echo "Erro ao obter token. Resposta da API:" >&2
            echo "$RESPONSE" >&2
        fi
        exit 1
    fi
    echo "$TOKEN"
}

# NOTA: quem chama faz `TOKEN=$(get_token) || exit 1`. O `exit 1` acima sai do
# subshell de $(...), nao do script -- sem a verificacao no chamador, o `auth`
# imprimia "Sucesso! Conexao estabelecida." logo depois da mensagem de erro,
# com o token vazio.

# ==============================================================================
# Comandos da API
# ==============================================================================

# Faz um GET autenticado e explica o 401, que e o caso confuso.
#
# Um token emitido e depois recusado nao e credencial errada: o /oauth/token
# validou o client_id e o secret para emitir. O que falha e a requisicao
# seguinte, quando o Koha vai procurar esse token.
#
# A causa vista em campo: Koha em modo CGI. O token vive no lado do servidor, e
# em CGI cada requisicao e um processo novo -- o que emitiu morre com a
# resposta. Sob Plack os workers persistem e o token sobrevive.
#
# Reconferir o secret nesse caso e olhar para a unica coisa que esta certa.
api_get() {
    local path=$1 accept=${2:-application/json}
    local token body status
    token=$(get_token) || exit 1

    body=$(curl -g -s -w $'\n%{http_code}' -X GET "${KOHA_URL}${path}" \
        -H "Authorization: Bearer $token" -H "Accept: ${accept}")
    status=${body##*$'\n'}
    body=${body%$'\n'*}

    if [ "$status" = "401" ]; then
        echo "O Koha emitiu um token e depois recusou esse mesmo token (HTTP 401)." >&2
        echo "As credenciais estao registradas -- o /oauth/token as validou" >&2
        echo "para emitir. O problema esta no servidor:" >&2
        echo "  - o Koha esta rodando em modo CGI. O token e guardado no lado" >&2
        echo "    do servidor, e em CGI cada requisicao e um processo novo:" >&2
        echo "    quem emitiu morre com a resposta, e o proximo procura num" >&2
        echo "    lugar vazio. A interface administrativa do Koha avisa disso," >&2
        echo "    e a correcao e do servidor: koha-plack --enable" >&2
        echo "  - ou o cadastro do usuario da API expirou" >&2
        echo "  - ou o usuario da API esta sem 'userid' preenchido" >&2
        echo "" >&2
        echo "Resposta: $body" >&2
        exit 1
    fi

    if [ "$status" = "403" ]; then
        echo "Autenticou, mas falta permissao (HTTP 403)." >&2
        echo "O usuario da API precisa da flag 'catalogue' no Koha." >&2
        echo "" >&2
        echo "Resposta: $body" >&2
        exit 1
    fi

    echo "$body" | jq .
}

list_biblios() {
    echo "=== Listando últimos Biblios na URL: $KOHA_URL ==="
    api_get "/api/v1/biblios?_per_page=5"
}

search_biblio() {
    QUERY=$1
    if [ -z "$QUERY" ]; then
        echo "Por favor, informe um termo de busca."
        echo "Uso: $0 search \"Nome do Livro\""
        exit 1
    fi

    echo "=== Buscando livros contendo título: '$QUERY' na URL: $KOHA_URL ==="
    
    # O Koha aceita buscas estruturadas em JSON no parâmetro 'q' mapeadas para SQL::Abstract.
    # O operador _match não é suportado em todas as versões. Usamos "like" com "%" para compatibilidade.
    JSON_QUERY="{\"title\":{\"like\":\"%$QUERY%\"}}"
    
    # api_get usa -g (--globoff) para o curl não interpretar as chaves {} do JSON
    api_get "/api/v1/biblios?q=$JSON_QUERY"
}

get_biblio_by_id() {
    BIBLIO_ID=$1
    if [ -z "$BIBLIO_ID" ]; then
        echo "Por favor, informe o ID do biblio."
        echo "Uso: $0 get_biblio 123"
        exit 1
    fi
    echo "=== Buscando Biblio ID: $BIBLIO_ID na URL: $KOHA_URL ==="
    # marc-in-json para obter todos os metadados puros
    api_get "/api/v1/biblios/${BIBLIO_ID}" "application/marc-in-json"
}

check_availability() {
    BIBLIO_ID=$1
    if [ -z "$BIBLIO_ID" ]; then
        echo "Por favor, informe o ID do biblio."
        echo "Uso: $0 availability 123"
        exit 1
    fi
    echo "=== Buscando Itens (Exemplares) do Biblio ID: $BIBLIO_ID na URL: $KOHA_URL ==="
    # A listagem física de exemplares (para ver availability)
    api_get "/api/v1/biblios/${BIBLIO_ID}/items"
}

auth_test() {
    echo "=== Testando Autenticação OAuth2 na URL: $KOHA_URL ==="
    TOKEN=$(get_token) || exit 1
    echo "Sucesso! Conexão estabelecida."
    echo "Bearer Token: $TOKEN"
}

# ==============================================================================
# Entrypoint
# ==============================================================================

case "$1" in
    auth)
        auth_test
        ;;
    list)
        list_biblios
        ;;
    search)
        search_biblio "$2"
        ;;
    get_biblio)
        get_biblio_by_id "$2"
        ;;
    availability)
        check_availability "$2"
        ;;
    *)
        echo "================================================================"
        echo " Koha REST API Tester"
        echo "================================================================"
        echo "Uso: $0 {auth|list|search|get_biblio|availability} [parametros]"
        echo ""
        echo "Comandos:"
        echo "  auth                     Testa a conexão e exibe o Bearer Token"
        echo "  list                     Lista os 5 primeiros biblios do catálogo"
        echo "  search \"Título\"          Busca livros por título"
        echo "  get_biblio <ID>          Retorna o MARC-in-JSON de um biblio"
        echo "  availability <ID>        Retorna os exemplares físicos de um biblio"
        echo ""
        echo "Variáveis de Ambiente Necessárias:"
        echo "  export KOHA_URL=\"http://localhost:8081\" (Padrão)"
        echo "  export CLIENT_ID=\"seu_client_id\""
        echo "  export CLIENT_SECRET=\"seu_secret\""
        echo "================================================================"
        exit 1
        ;;
esac

