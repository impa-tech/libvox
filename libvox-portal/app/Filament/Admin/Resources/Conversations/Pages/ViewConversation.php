<?php

namespace App\Filament\Admin\Resources\Conversations\Pages;

use App\Filament\Admin\Resources\Conversations\ConversationResource;
use App\Models\Conversation;
use Filament\Resources\Pages\ViewRecord;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\HtmlString;
use Phiki\Grammar\Grammar;
use Phiki\Phiki;
use Phiki\Theme\Theme;
use Symfony\Component\HttpFoundation\Response;

/**
 * One conversation, whole — including what the tools returned.
 *
 * The tool results are the point. "Why did she say that?" is answered by what
 * she was looking at when she said it, and an answer that contradicts the
 * records above it is exactly what this screen exists to catch.
 */
class ViewConversation extends ViewRecord
{
    protected static string $resource = ConversationResource::class;

    protected string $view = 'filament.admin.conversation-transcript';

    public function getTitle(): string
    {
        return __('portal.conversation.transcript');
    }

    /**
     * The library being read, kept so it survives a Livewire request.
     *
     * `mount()` runs once. Every Livewire request after it is a hydration,
     * `resolveRecord` does not run again, and the tenant scope set there would
     * be gone — row-level security would then hide the very messages the page
     * is drawing. `booted()` re-binds it on each one. This was found through a
     * modal that has since been folded into the page; the trap is the
     * hydration, not the modal, so the binding stays.
     *
     * Public, and therefore editable by whoever is looking at the page. That is
     * the same standing the filter on the listing has, and the note below says
     * why it is not a credential: choosing a library reveals nothing that
     * choosing it in the filter would not, and the policy still refuses
     * everything else.
     */
    public ?string $tenant = null;

    public function booted(): void
    {
        if (filled($this->tenant)) {
            Conversation::scopeToTenant($this->tenant);
        }
    }

    /**
     * Every tool call's JSON, highlighted, ready to sit on the page.
     *
     * It used to be a modal, one call at a time. Reading a transcript is
     * comparing calls -- what was asked here against what came back there --
     * and a modal makes that a matter of memory: open, read, close, open the
     * next, try to remember the first. So both halves of every call are in the
     * document, and the page decides what is visible.
     *
     * Both halves, because a bad search is usually a bad query and the
     * arguments are the only place that shows.
     *
     * Highlighted here rather than in the browser, with the same Phiki and the
     * same two themes Filament's own CodeEntry uses, so the blocks look like
     * the ones the modal drew. A conversation carries a handful of calls; this
     * is a handful of tokenizations per render and no JavaScript.
     *
     * The raw string travels beside the HTML: it is what the copy button puts
     * on the clipboard, and highlighted markup is not what anyone wants pasted.
     *
     * @return array<int, array<string, array{raw: string, html: HtmlString}>>
     */
    public function toolJson(): array
    {
        $phiki = new Phiki;
        $blocks = [];
        $call = null;

        foreach ($this->record->messages as $message) {
            if ($message->role === 'assistant' && filled($message->tool_name)) {
                $call = $message->message_id;
                $blocks[$call] = [];

                if (filled($message->tool_arguments)) {
                    $blocks[$call]['sent'] = $this->code($phiki, $message->tool_arguments);
                }
            } elseif ($message->role === 'tool' && $call !== null) {
                $blocks[$call]['came_back'] = $this->code($phiki, $message->result());
                $call = null;
            }
        }

        return $blocks;
    }

    /** @return array{raw: string, html: HtmlString} */
    private function code(Phiki $phiki, ?array $value): array
    {
        $raw = $this->asJson($value);

        return [
            'raw' => $raw,
            'html' => new HtmlString((string) $phiki->codeToHtml($raw, Grammar::Json, [
                'light' => Theme::GithubLight,
                'dark' => Theme::GithubDarkHighContrast,
            ])),
        ];
    }

    /**
     * Each tool call paired with the result that answered it.
     *
     * One pass over the collection the page already loaded, rather than a query
     * per call: the two rows are adjacent by construction — the agent loop
     * appends a call and its result per intent — so walking the list in order
     * pairs them without asking the database anything.
     *
     * Keyed by the call's message_id, because the blade meets the call first
     * and skips the result row.
     *
     * @return array<int, array|null>
     */
    public function toolResults(): array
    {
        $paired = [];
        $awaiting = null;

        foreach ($this->record->messages as $message) {
            if ($message->role === 'assistant' && filled($message->tool_name)) {
                $awaiting = $message->message_id;
                $paired[$awaiting] = null;
            } elseif ($message->role === 'tool' && $awaiting !== null) {
                $paired[$awaiting] = $message->result();
                $awaiting = null;
            }
        }

        return $paired;
    }

    private function asJson(?array $value): string
    {
        return json_encode(
            $value ?? [],
            JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES,
        );
    }

    /**
     * Bind before resolving, because without a binding there is no record to
     * resolve — the policy hides it.
     *
     * The library travels in the query string, put there by the listing's own
     * link. It is a choice of what to look at and not a credential: binding to
     * a library reveals nothing that choosing it in the filter would not, and
     * the policy still refuses everything else.
     */
    protected function resolveRecord(int|string $key): Model
    {
        $tenantId = request()->query('tenant');

        abort_if(blank($tenantId), Response::HTTP_NOT_FOUND);

        $this->tenant = (string) $tenantId;
        Conversation::scopeToTenant($this->tenant);

        return parent::resolveRecord($key);
    }
}
