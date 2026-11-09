<?php

namespace App\Models;

use App\Database\ConversationScope;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;
use Illuminate\Support\Facades\DB;
use LogicException;
use RuntimeException;

/**
 * One session's conversation with MARC.IA, as /libvox-core stored it.
 *
 * Read here to answer one question — *is she answering correctly?* — which
 * cannot be judged without reading what she answered. That is why this lives in
 * the operator's panel and not a library's.
 *
 * **Scoped to a session, never to a person.** There is no column for a name, an
 * email, a phone number, an IP or a platform identifier, and that absence is
 * the enforcement rather than a convention. What is durable here is what
 * someone asked a library, which is why retention is a command
 * (`libvox-core prune`) and not a suggestion.
 *
 * Read-only. /libvox-core owns this schema and writes it; an edit from here
 * would put a second author on a record only one component is supposed to
 * write.
 */
class Conversation extends Model
{
    protected $connection = 'pgsql_conversations';

    protected $table = 'conversations';

    protected $primaryKey = 'conversation_id';

    public $incrementing = false;

    protected $keyType = 'string';

    public $timestamps = false;

    protected function casts(): array
    {
        return [
            'started_at' => 'datetime',
            'last_message_at' => 'datetime',
        ];
    }

    public function messages(): HasMany
    {
        // `rating` eagerly, because the transcript asks every assistant message
        // for one: twenty-six messages was twenty-six queries.
        return $this->hasMany(ConversationMessage::class, 'conversation_id', 'conversation_id')
            ->with('rating')
            ->orderBy('message_id');
    }

    public function save(array $options = []): bool
    {
        throw new LogicException(self::WHY_READ_ONLY);
    }

    public function delete(): bool
    {
        throw new LogicException(self::WHY_READ_ONLY);
    }

    private const WHY_READ_ONLY =
        'The conversation log belongs to /libvox-core, which is its one writer. '
        .'Deleting a transcript is retention, and retention is `libvox-core '
        .'prune` -- a schedule, not a button.';

    /**
     * Bind the conversation connection to one library.
     *
     * The operator's panel binds no tenant of its own, so without this the
     * tables read empty -- fail-closed, and the reason a library has to be
     * chosen before anything is listed. Reading across libraries at once would
     * mean a connection that ignores row-level security, held by the
     * application that serves requests; this reads one library at a time
     * instead.
     */
    public static function scopeToTenant(string $tenantId): void
    {
        $role = DB::connection('pgsql_conversations')->selectOne(
            'SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user'
        );

        throw_if($role === null || $role->rolsuper || $role->rolbypassrls, new RuntimeException(
            'LIBVOX_CONVERSATION_DB_DSN connects as a role that bypasses '
            .'row-level security, so choosing a library would not limit what is '
            .'read. Use libvox_app.'
        ));

        app(ConversationScope::class)->bind($tenantId);
    }
}
