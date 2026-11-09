<?php

namespace App\Database;

/**
 * The tenant binding for the conversation log's connection.
 *
 * A subclass for the same reason DiscoveryScope is one: Laravel's container
 * resolves singletons by class name, so two bindings of TenantScope could not
 * be told apart. The behaviour is inherited whole.
 *
 * This is the one that carries the most weight. `conversations` and `messages`
 * hold what people asked a library, and the operator panel -- which binds no
 * tenant of its own -- reads them one library at a time precisely so that
 * forgetting to bind shows nothing rather than showing everything.
 */
class ConversationScope extends TenantScope {}
