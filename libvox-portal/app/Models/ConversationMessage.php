<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasOne;
use LogicException;

/**
 * One turn: what was asked, what was answered, or what a tool returned.
 *
 * `role = 'tool'` carries the tool's result as JSON, which is what lets a later
 * turn know which book an earlier one was about — and, here, what lets someone
 * asking *"why did she say that?"* see what she was looking at when she said
 * it. A failed call is stored the same way, as `{"error": ...}`.
 *
 * Read-only, for the same reason as Conversation.
 */
class ConversationMessage extends Model
{
    protected $connection = 'pgsql_conversations';

    protected $table = 'messages';

    protected $primaryKey = 'message_id';

    public $timestamps = false;

    protected function casts(): array
    {
        return [
            'tool_arguments' => 'array',
            'created_at' => 'datetime',
        ];
    }

    public function save(array $options = []): bool
    {
        throw new LogicException('The conversation log is written by /libvox-core.');
    }

    public function delete(): bool
    {
        throw new LogicException('Deleting a transcript is `libvox-core prune`.');
    }

    /**
     * Whether a reader said this answer helped, or null if nobody said.
     *
     * Null is the ordinary case by a wide margin: a rating is one click that
     * nobody is required to make. Absent means unrated, never "not useful".
     */
    public function rating(): HasOne
    {
        return $this->hasOne(MessageRating::class, 'message_id', 'message_id');
    }

    /** The tool's own answer, decoded, or null when this is not a tool turn. */
    public function result(): ?array
    {
        if ($this->role !== 'tool' || $this->content === null) {
            return null;
        }

        $decoded = json_decode($this->content, true);

        return is_array($decoded) ? $decoded : null;
    }
}
