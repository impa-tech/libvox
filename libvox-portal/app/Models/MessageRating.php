<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use LogicException;

/**
 * Whether one answer helped, as a reader said so in the widget.
 *
 * Three columns and no fourth. There is no "who rated", no session id, no
 * device fingerprint, no IP, and no free-text field — what someone thought of
 * an answer is more sensitive than the answer, not less, and the absence of a
 * column is the enforcement. The key is the message itself, so a reader who
 * changed their mind replaced a row rather than leaving two an hour apart,
 * which would have been a weak fingerprint saying the same person was here
 * twice.
 *
 * **Read here, never written.** /libvox-core owns this table and the widget is
 * the only thing that fills it. An operator marking an answer useful would be
 * an operator answering a question a reader was asked.
 */
class MessageRating extends Model
{
    protected $connection = 'pgsql_conversations';

    protected $table = 'message_ratings';

    protected $primaryKey = 'message_id';

    public $incrementing = false;

    protected $keyType = 'int';

    public $timestamps = false;

    protected function casts(): array
    {
        return [
            'useful' => 'boolean',
            'rated_at' => 'datetime',
        ];
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
        'A rating is what a reader said in the widget. Nothing in this panel '
        .'may write one, and forgetting it is retention: it cascades with the '
        .'conversation under `libvox-core prune`.';
}
