<?php

use App\Filament\Admin\Resources\Conversations\Pages\ViewConversation;
use App\Models\Conversation;
use App\Models\ConversationMessage;
use Filament\Facades\Filament;
use Illuminate\Support\Facades\DB;

/*
 * The transcript, and the machinery folded into it.
 *
 * This screen had no test that opened it at all -- not in the render sweep,
 * not here -- while carrying a rewrite and a modal. That absence is what the
 * first test below closes. The modal is gone now: the JSON sits in the page so
 * every call can be open at once, which is what comparing calls needs.
 */

beforeEach(function () {
    if (! conversation_db_is_reachable()) {
        test()->markTestSkipped('no conversation log; run libvox-core migrations');
    }

    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();
    conversations()->statement('TRUNCATE conversations CASCADE');
    Filament::setCurrentPanel('admin');
});

/** A turn with one tool call: a question, a call, its result, an answer. */
function aTurnWithATool(string $tenantId = 'lib-furg'): string
{
    return storeConversation($tenantId, [
        ['role' => 'user', 'content' => 'tem algo do Machado?'],
        ['role' => 'assistant', 'content' => '', 'tool_name' => 'search_catalog'],
        ['role' => 'tool', 'content' => FOUND_NOTHING, 'tool_name' => 'search_catalog'],
        ['role' => 'assistant', 'content' => "Nada sob esse termo.\nTente outro."],
    ]);
}

/** The transcript page, opened the way the listing's link opens it. */
function transcriptOf(string $conversationId, string $tenantId = 'lib-furg')
{
    return Livewire\Livewire::withQueryParams(['tenant' => $tenantId])
        ->actingAs(operator())
        ->test(ViewConversation::class, ['record' => $conversationId]);
}

/** The tool call in a conversation, read with the binding a test has to set. */
function theToolCall(string $conversationId): ConversationMessage
{
    Conversation::scopeToTenant('lib-furg');

    return ConversationMessage::query()
        ->where('conversation_id', $conversationId)
        ->whereNotNull('tool_name')
        ->where('role', 'assistant')
        ->firstOrFail();
}

test('the transcript opens', function () {
    $id = aTurnWithATool();

    transcriptOf($id)
        ->assertOk()
        ->assertSee('tem algo do Machado?')
        ->assertSee('search_catalog');
});

test('a call and its result are one row, not two', function () {
    // The point of the rewrite: 784 rows of machinery against 423 of
    // conversation, across the whole log. The result is drawn as part of its
    // call, so the pairing is what has to be right.
    $id = aTurnWithATool();

    $paired = transcriptOf($id)->instance()->toolResults();

    expect($paired)->toHaveCount(1)
        ->and(array_values($paired)[0])->toMatchArray(['records' => []]);
});

test('both halves of every call are in the page, not behind a modal', function () {
    // Reading a transcript is comparing calls, and a modal makes that a matter
    // of memory. The JSON is in the document; the toggles only decide what
    // shows, so `abrir todos` puts every call on screen at once.
    $id = aTurnWithATool();

    $page = transcriptOf($id);

    $page->assertOk()
        ->assertSee(__('portal.conversation.came_back'))
        ->assertSee(__('portal.conversation.expand_all'))
        // Highlighted markup, so the JSON arrives split across spans. The
        // strings inside it are what survives, and one of them is the whole
        // finding on this screen: she answered over an empty result.
        ->assertSee('index_status')
        ->assertSee('records')
        // And nothing is left mounting an action that no longer exists.
        ->assertDontSee('viewToolCall');

    $call = theToolCall($id);
    $blocks = $page->instance()->toolJson();

    expect($blocks)->toHaveCount(1)
        ->and($blocks[$call->message_id]['came_back']['raw'])
        ->toContain('"index_status": "ready"');
});

test('the page still draws after a hydration, when the tenant scope would be gone', function () {
    // The trap this page had waiting, found through the modal and outliving
    // it. `mount()` runs once; every Livewire request after it is a hydration,
    // `resolveRecord` does not run again, and without the tenant re-bound
    // row-level security hides the very messages the page is drawing.
    // `booted()` is what re-binds it.
    $id = aTurnWithATool();

    $page = transcriptOf($id);

    // Drop the binding the way a fresh request would, then act.
    DB::connection('pgsql_conversations')->select("select set_config('libvox.tenant_id', '', false)");

    $page->call('$refresh')->assertOk();

    expect($page->instance()->toolResults())->toHaveCount(1)
        ->and($page->instance()->toolJson())->toHaveCount(1);
});

test('the privacy note is gone from the screen', function () {
    // Removed as unnecessary: the operator reading this screen is the person
    // who already knows. The ninety days live in /libvox-core's README, in
    // store.prune's docstring, and in both read-only models.
    $id = aTurnWithATool();

    transcriptOf($id)
        ->assertDontSee('libvox-core prune');
});
