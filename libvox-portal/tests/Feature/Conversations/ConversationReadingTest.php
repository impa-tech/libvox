<?php

use App\Filament\Admin\Resources\Conversations\Pages\ListConversations;
use App\Models\Conversation;
use App\Models\ConversationMessage;
use App\Models\MessageRating;
use App\Models\Tenant;
use Filament\Facades\Filament;
use Illuminate\Support\Facades\DB;

/*
 * Reading the conversation log from the operator's panel.
 *
 * The panel binds no tenant of its own, and both tables carry FORCE ROW LEVEL
 * SECURITY. So the thing worth asserting is not that a filter works -- it is
 * that the database is what limits what is read, and that forgetting to choose
 * a library shows nothing rather than everything.
 */

beforeEach(function () {
    if (! conversation_db_is_reachable()) {
        test()->markTestSkipped('no conversation log; run libvox-core migrations');
    }

    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();

    conversations()->statement('TRUNCATE conversations CASCADE');
});

test('an unbound connection reads no conversations at all', function () {
    storeConversation('lib-furg', [['role' => 'user', 'content' => 'oi']]);

    DB::connection('pgsql_conversations')->select("select set_config('libvox.tenant_id', '', false)");

    expect(Conversation::query()->count())->toBe(0);
});

test('choosing a library shows that library conversations and no other', function () {
    storeConversation('lib-furg', [['role' => 'user', 'content' => 'tem Machado?']]);
    storeConversation('lib-ibict', [['role' => 'user', 'content' => 'tem Rosa?']]);

    Conversation::scopeToTenant('lib-furg');

    $asked = ConversationMessage::query()->where('role', 'user')->pluck('content')->all();

    expect($asked)->toBe(['tem Machado?']);
});

test('the connection cannot bypass row level security', function () {
    // Without this the filter would be a suggestion: a privileged role reads
    // every library's conversations whatever is chosen above the table.
    $role = DB::connection('pgsql_conversations')->selectOne(
        'SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user'
    );

    expect($role->rolsuper)->toBeFalse()->and($role->rolbypassrls)->toBeFalse();
});

test('the conversation log is a read model, and refuses to be edited', function () {
    storeConversation('lib-furg', [['role' => 'user', 'content' => 'oi']]);
    Conversation::scopeToTenant('lib-furg');

    $conversation = Conversation::query()->first();
    $message = ConversationMessage::query()->first();

    expect(fn () => $conversation->delete())->toThrow(LogicException::class)
        ->and(fn () => $message->save())->toThrow(LogicException::class);
});

test('nothing the listing exposes identifies a person', function () {
    storeConversation('lib-furg', [['role' => 'user', 'content' => 'oi']]);
    Conversation::scopeToTenant('lib-furg');

    $columns = array_keys(Conversation::query()->first()->getAttributes());

    expect($columns)->toBe(['conversation_id', 'tenant_id', 'channel', 'started_at', 'last_message_at']);

    foreach (['name', 'email', 'phone', 'ip', 'user_id', 'session'] as $forbidden) {
        expect(implode(',', $columns))->not->toContain($forbidden);
    }
});

test('a search that came back empty is findable', function () {
    // The whole reason the screen exists: MARC.IA saying "the library has
    // none" is only wrong when the search that told her so came back empty.
    storeConversation('lib-furg', [
        ['role' => 'user', 'content' => 'tem pokemon?'],
        ['role' => 'tool', 'content' => FOUND_NOTHING, 'tool_name' => 'search_catalog'],
    ]);
    storeConversation('lib-furg', [
        ['role' => 'tool', 'content' => '{"records": [{"biblio_id": 1}]}', 'tool_name' => 'search_catalog'],
    ]);

    Conversation::scopeToTenant('lib-furg');

    $empty = ConversationMessage::query()
        ->where('role', 'tool')
        ->whereRaw('content LIKE \'%"records": []%\'')
        ->count();

    expect($empty)->toBe(1);
});

test('a tool that failed is findable', function () {
    storeConversation('lib-furg', [
        ['role' => 'tool', 'content' => TOOL_BROKE, 'tool_name' => 'check_availability'],
    ]);

    Conversation::scopeToTenant('lib-furg');

    expect(ConversationMessage::query()->whereRaw('content LIKE \'%"error"%\'')->count())->toBe(1);
});

test('without a library chosen the screen lists nothing', function () {
    // Not "reads empty because the binding was forgotten" -- decided, and the
    // subheading says which library is being read or asks for one.
    storeConversation('lib-furg', [['role' => 'user', 'content' => 'oi']]);

    // Fetched *with* a binding, so the assertion below is about the screen and
    // not about an unbound query that would come back empty either way. A test
    // that passes because it asked for nothing proves nothing.
    Conversation::scopeToTenant('lib-furg');
    $existing = Conversation::query()->get();
    expect($existing)->toHaveCount(1);

    Filament::setCurrentPanel('admin');

    $page = Livewire\Livewire::actingAs(operator())->test(ListConversations::class);

    $page->assertOk()->assertCanNotSeeTableRecords($existing);

    expect($page->instance()->chosenLibrary())->toBeNull()
        ->and($page->instance()->getSubheading())->toBe(__('portal.conversation.choose'));
});

// --- whether the answer helped ---------------------------------------------

test('the listing marks a conversation a reader said did not help', function () {
    $helped = storeConversation('lib-furg', [
        ['role' => 'user', 'content' => 'tem Machado?'],
        ['role' => 'assistant', 'content' => 'Temos dois.'],
    ]);
    $missed = storeConversation('lib-furg', [
        ['role' => 'user', 'content' => 'tem xadrez?'],
        ['role' => 'assistant', 'content' => 'Nada sobre xadrez.'],
    ]);
    rateLastAnswer($helped, true);
    rateLastAnswer($missed, false);

    Conversation::scopeToTenant('lib-furg');

    expect(ConversationMessage::query()
        ->whereIn('message_id', DB::connection('pgsql_conversations')
            ->table('message_ratings')->select('message_id')->where('useful', false))
        ->pluck('content')->all())
        ->toBe(['Nada sobre xadrez.']);
});

test('the pairing the feature exists for: not useful, over an empty search', function () {
    // Either filter alone is a list. Together they are a finding -- "she told a
    // reader the library has nothing and the reader disagreed" -- which is a
    // catalogue gap, an indexing gap, or a search that failed on the word the
    // person used. This asserts the two can be read as one.
    $disagreed = storeConversation('lib-furg', [
        ['role' => 'user', 'content' => 'tem xadrez?'],
        ['role' => 'assistant', 'content' => '', 'tool_name' => 'search_catalog'],
        ['role' => 'tool', 'content' => FOUND_NOTHING, 'tool_name' => 'search_catalog'],
        ['role' => 'assistant', 'content' => 'A biblioteca não tem nada sobre xadrez.'],
    ]);
    // Unhappy, but the search did find something: a different problem.
    $other = storeConversation('lib-furg', [
        ['role' => 'user', 'content' => 'tem Machado?'],
        ['role' => 'assistant', 'content' => 'Temos dois.'],
    ]);
    rateLastAnswer($disagreed, false);
    rateLastAnswer($other, false);

    Conversation::scopeToTenant('lib-furg');

    $unhelpful = ConversationMessage::query()
        ->whereIn('message_id', DB::connection('pgsql_conversations')
            ->table('message_ratings')->select('message_id')->where('useful', false))
        ->pluck('conversation_id')->all();

    $emptySearch = ConversationMessage::query()
        ->where('role', 'tool')
        ->whereRaw('content LIKE \'%"records": []%\'')
        ->pluck('conversation_id')->all();

    expect(array_values(array_intersect($unhelpful, $emptySearch)))->toBe([$disagreed]);
});

test('one library never sees another library s ratings', function () {
    // The policy is reached through the message, which is reached through the
    // conversation -- a third hop, and the one this table adds.
    $mine = storeConversation('lib-furg', [
        ['role' => 'user', 'content' => 'meu'],
        ['role' => 'assistant', 'content' => 'Minha resposta.'],
    ]);
    $hers = storeConversation('lib-ibict', [
        ['role' => 'user', 'content' => 'dela'],
        ['role' => 'assistant', 'content' => 'Resposta dela.'],
    ]);
    rateLastAnswer($mine, true);
    rateLastAnswer($hers, false);

    Conversation::scopeToTenant('lib-furg');

    expect(DB::connection('pgsql_conversations')->table('message_ratings')->count())->toBe(1);
});

test('an answer nobody rated is unrated, never not useful', function () {
    // Null is the ordinary case by a wide margin: a rating is one click nobody
    // is required to make, and a screen that read absence as a complaint would
    // report every conversation as a failure.
    $id = storeConversation('lib-furg', [
        ['role' => 'user', 'content' => 'tem Machado?'],
        ['role' => 'assistant', 'content' => 'Temos dois.'],
    ]);

    Conversation::scopeToTenant('lib-furg');

    $answer = ConversationMessage::query()
        ->where('conversation_id', $id)->where('role', 'assistant')->firstOrFail();

    expect($answer->rating)->toBeNull();
});

test('a rating cannot be written from the operator s panel', function () {
    $id = storeConversation('lib-furg', [
        ['role' => 'user', 'content' => 'tem Machado?'],
        ['role' => 'assistant', 'content' => 'Temos dois.'],
    ]);
    rateLastAnswer($id, false);

    Conversation::scopeToTenant('lib-furg');

    $rating = MessageRating::query()->firstOrFail();

    // An operator marking an answer useful would be an operator answering a
    // question a reader was asked.
    expect(fn () => $rating->save())->toThrow(LogicException::class)
        ->and(fn () => $rating->delete())->toThrow(LogicException::class);
});

test('the column renders every state, and the filter selects what it shows', function () {
    // Four records, one per thing the column can say, all asserted through the
    // column itself.
    //
    // Two of them used to be asserted against a copy of the query living in
    // Pest.php, which proved the SQL and not the screen -- and the mixed case,
    // the one that most needed proving, was only ever checked that way. The
    // copy is gone.
    //
    // The column and the filter have to agree. A filter that listed a
    // conversation the column labels *negativa* would be two different answers
    // to the same question on one screen, so `positive` excludes the mixed
    // ones -- which is where the aggregate and the query could quietly drift.
    $good = storeConversation('lib-furg', [
        ['role' => 'user', 'content' => 'a'], ['role' => 'assistant', 'content' => 'Ajudou.'],
    ]);
    $bad = storeConversation('lib-furg', [
        ['role' => 'user', 'content' => 'b'], ['role' => 'assistant', 'content' => 'Não ajudou.'],
    ]);
    $silent = storeConversation('lib-furg', [
        ['role' => 'user', 'content' => 'c'], ['role' => 'assistant', 'content' => 'Ninguém disse.'],
    ]);
    // Both signs on one conversation. Not hypothetical: two of the seven rated
    // conversations on the log this was written against carry both, and the
    // listing exists to find the ones worth opening -- a reader helped twice
    // and failed once is one of them.
    $mixed = storeConversation('lib-furg', [
        ['role' => 'user', 'content' => 'd'],
        ['role' => 'assistant', 'content' => 'Primeira, ajudou.'],
        ['role' => 'user', 'content' => 'e'],
        ['role' => 'assistant', 'content' => 'Segunda, não ajudou.'],
    ]);

    rateLastAnswer($good, true);
    rateLastAnswer($bad, false);

    Conversation::scopeToTenant('lib-furg');
    $both = ConversationMessage::query()
        ->where('conversation_id', $mixed)->where('role', 'assistant')
        ->orderBy('message_id')->pluck('message_id')->all();
    conversations()->insert(
        'INSERT INTO message_ratings (message_id, useful) VALUES (?, true), (?, false)',
        [$both[0], $both[1]],
    );

    $page = Livewire\Livewire::actingAs(operator())
        ->test(ListConversations::class)
        ->filterTable('tenant_id', 'lib-furg');

    $page->assertTableColumnStateSet('rating', true, Conversation::find($good))
        ->assertTableColumnStateSet('rating', false, Conversation::find($bad))
        ->assertTableColumnStateSet('rating', null, Conversation::find($silent))
        // The one the whole three-state design turns on.
        ->assertTableColumnStateSet('rating', false, Conversation::find($mixed));

    $page->filterTable('rating', 'negative')
        ->assertCanSeeTableRecords([Conversation::find($bad), Conversation::find($mixed)])
        ->assertCanNotSeeTableRecords([Conversation::find($good), Conversation::find($silent)]);

    // The mixed one is absent here, and that is the point: the column calls it
    // negativa, so the filter must not offer it as positiva.
    $page->filterTable('rating', 'positive')
        ->assertCanSeeTableRecords([Conversation::find($good)])
        ->assertCanNotSeeTableRecords([Conversation::find($bad), Conversation::find($mixed)]);

    $page->filterTable('rating', 'none')
        ->assertCanSeeTableRecords([Conversation::find($silent)])
        ->assertCanNotSeeTableRecords([Conversation::find($good), Conversation::find($bad)]);
});
