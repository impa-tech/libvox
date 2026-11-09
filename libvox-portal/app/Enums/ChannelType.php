<?php

namespace App\Enums;

/**
 * The ways a reader can reach a library.
 *
 * Paired with the CHECK on channel_bindings.channel. What differs between them
 * is not the conversation -- same agent, same tools, same catalog -- but what
 * each platform imposes: how the tenant is identified, whether a secret is
 * involved, whether an origin can be checked.
 */
enum ChannelType: string
{
    /** The widget embedded in a library's OPAC. Its key is public. */
    case Web = 'web';

    case Telegram = 'telegram';

    case WhatsApp = 'whatsapp';

    case Email = 'email';

    public function label(): string
    {
        return __('portal.channel.type.'.$this->value);
    }

    /**
     * Whether this channel authenticates with a credential LibVox holds.
     *
     * The web widget does not: its key identifies a library, and what
     * authorizes the connection is the Origin allowlist. Enforced by the
     * channel_bindings_secret_shape constraint, not only by this method.
     */
    public function requiresSecret(): bool
    {
        return match ($this) {
            self::Web, self::Email => false,
            self::Telegram, self::WhatsApp => true,
        };
    }

    /** Only the web channel is reached from a page with an Origin header. */
    public function usesOrigins(): bool
    {
        return $this === self::Web;
    }

    /**
     * What the library must obtain before this channel can be switched on.
     *
     * Every channel except the web widget needs something only the library can
     * create, which is why channels are opt-in per library rather than a
     * platform-wide switch.
     */
    public function credentialHint(): string
    {
        return __('portal.channel.hint.'.$this->value);
    }
}
