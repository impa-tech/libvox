<?php

namespace App\Filament\Admin\Resources\Conversations\Pages;

use App\Filament\Admin\Resources\Conversations\ConversationResource;
use App\Models\Conversation;
use Filament\Resources\Pages\ListRecords;
use Illuminate\Database\Eloquent\Builder;

class ListConversations extends ListRecords
{
    protected static string $resource = ConversationResource::class;

    public function getTitle(): string
    {
        return __('portal.conversation.label_plural');
    }

    public function getSubheading(): ?string
    {
        return $this->chosenLibrary() === null
            ? __('portal.conversation.choose')
            : __('portal.conversation.reading', ['library' => $this->chosenLibrary()]);
    }

    /**
     * Bind the connection to the chosen library, and list nothing until one is.
     *
     * The binding happens here rather than in the panel's middleware for two
     * reasons. It needs a library the operator picks, which middleware has no
     * way to know; and `bind` runs a query and therefore opens the connection,
     * so binding per request would let the conversation database being down
     * take the whole operator panel with it instead of this one screen.
     *
     * `whereRaw('false')` when nothing is chosen, rather than trusting the
     * unbound connection to read empty. It does read empty -- the policy is
     * fail-closed -- but a screen that shows nothing because a `false` was
     * asked for and a screen that shows nothing because a binding was forgotten
     * look identical, and only one of them is a decision.
     */
    protected function getTableQuery(): ?Builder
    {
        $tenantId = $this->chosenLibrary();

        if ($tenantId === null) {
            return Conversation::query()->whereRaw('false');
        }

        Conversation::scopeToTenant($tenantId);

        return parent::getTableQuery();
    }

    /** The library chosen in the filter, or null. */
    public function chosenLibrary(): ?string
    {
        $value = $this->getTableFilterState('tenant_id')['value'] ?? null;

        return filled($value) ? (string) $value : null;
    }
}
