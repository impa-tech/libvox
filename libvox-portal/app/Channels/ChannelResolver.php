<?php

namespace App\Channels;

use App\Enums\ChannelType;
use Illuminate\Database\ConnectionInterface;

/**
 * Which library owns this channel binding.
 *
 * A thin wrapper over resolve_channel_binding(), which is where the logic
 * actually lives -- deliberately, because /libvox-core is Python and will call
 * the same function. A rule implemented here instead of there would be a rule
 * only one of the two callers has.
 *
 * The portal itself has no reason to resolve a binding at runtime: this exists
 * so the contract has a caller, a test and a name before /libvox-core arrives
 * to depend on it.
 */
class ChannelResolver
{
    public function __construct(private readonly ConnectionInterface $connection) {}

    /**
     * @param  string|null  $origin  The Origin header, as the browser sent it.
     *                               Required for the web channel and ignored
     *                               for every other.
     * @return string|null The tenant_id, or null. An unknown key, a channel
     *                     that is off, a suspended library and a page not on
     *                     the allowlist are all the same answer.
     */
    public function resolve(ChannelType $channel, string $bindingKey, ?string $origin = null): ?string
    {
        $row = $this->connection->selectOne(
            'SELECT resolve_channel_binding(?, ?, ?) AS tenant_id',
            [$channel->value, $bindingKey, $origin],
        );

        return $row?->tenant_id;
    }
}
