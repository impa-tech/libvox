<?php

namespace App\Filament\Admin\Resources\Conversations\Tables;

use App\Filament\Admin\Resources\Conversations\ConversationResource;
use App\Models\ConversationMessage;
use App\Models\MessageRating;
use App\Models\Tenant;
use Filament\Actions\ViewAction;
use Filament\Tables\Columns\IconColumn;
use Filament\Tables\Columns\TextColumn;
use Filament\Tables\Filters\Filter;
use Filament\Tables\Filters\SelectFilter;
use Filament\Tables\Table;
use Illuminate\Database\Eloquent\Builder;

class ConversationsTable
{
    public static function configure(Table $table): Table
    {
        return $table
            ->columns([
                TextColumn::make('started_at')
                    ->label(__('portal.conversation.started'))
                    ->dateTime('d/m/Y H:i')
                    ->sortable(),

                TextColumn::make('channel')
                    ->label(__('portal.conversation.channel'))
                    ->badge(),

                TextColumn::make('turns')
                    ->label(__('portal.conversation.turns'))
                    ->state(fn ($record) => $record->messages()->where('role', 'user')->count())
                    ->alignCenter(),

                IconColumn::make('unanswered')
                    ->label(__('portal.conversation.unanswered'))
                    ->boolean()
                    ->state(fn ($record) => self::hasEmptySearch($record->conversation_id))
                    ->tooltip(__('portal.conversation.unanswered_help')),

                IconColumn::make('failed')
                    ->label(__('portal.conversation.failed'))
                    ->boolean()
                    ->state(fn ($record) => self::hasFailedTool($record->conversation_id))
                    ->tooltip(__('portal.conversation.failed_help')),

                TextColumn::make('rating')
                    ->label(__('portal.conversation.rating'))
                    ->badge()
                    ->state(fn ($record) => self::ratingOf($record->conversation_id))
                    ->color(fn (?bool $state): string => $state ? 'success' : 'danger')
                    ->formatStateUsing(fn (bool $state): string => $state
                        ? __('portal.conversation.rated_positive')
                        : __('portal.conversation.rated_negative'))
                    // Blank, not a dash and not "neutral": nobody said anything,
                    // and a rating is one click nobody is required to make. Most
                    // conversations will always be blank here.
                    ->placeholder('')
                    ->tooltip(__('portal.conversation.rating_help')),

                TextColumn::make('last_message_at')
                    ->label(__('portal.conversation.last'))
                    ->since()
                    ->sortable()
                    ->toggleable(),
            ])
            ->defaultSort('last_message_at', 'desc')
            ->filters([
                SelectFilter::make('tenant_id')
                    ->label(__('portal.conversation.library'))
                    ->options(fn () => Tenant::query()->orderBy('tenant_id')->pluck('tenant_id', 'tenant_id'))
                    // A no-op on purpose. This filter *chooses* the library;
                    // what *enforces* it is the row-level security binding the
                    // page applies before the query runs. Adding a where clause
                    // here would suggest the clause is what protects the data,
                    // and someone would eventually remove it thinking it
                    // redundant.
                    ->query(fn (Builder $query): Builder => $query),

                Filter::make('unanswered')
                    ->label(__('portal.conversation.only_unanswered'))
                    ->query(fn (Builder $query): Builder => $query->whereIn(
                        'conversation_id',
                        self::conversationsWhere("content LIKE '%\"records\": []%'"),
                    )),

                Filter::make('failed')
                    ->label(__('portal.conversation.only_failed'))
                    ->query(fn (Builder $query): Builder => $query->whereIn(
                        'conversation_id',
                        self::conversationsWhere("content LIKE '%\"error\"%'"),
                    )),

                // The one that pays for the feature. Read with the empty-search
                // filter above it, a negative is "she told a reader the library
                // has nothing and the reader disagreed" -- a catalogue gap, an
                // indexing gap, or a search that failed on the word they used.
                // Each filter alone is a list; the two together are a finding.
                //
                // **It selects exactly what the column shows, which is why
                // `positive` excludes the mixed ones.** A filter that listed a
                // conversation the column labels *negativa* would be two
                // different answers to the same question on one screen.
                SelectFilter::make('rating')
                    ->label(__('portal.conversation.rating'))
                    ->options([
                        'negative' => __('portal.conversation.rated_negative'),
                        'positive' => __('portal.conversation.rated_positive'),
                        'none' => __('portal.conversation.rated_none'),
                    ])
                    ->query(fn (Builder $query, array $data): Builder => match ($data['value'] ?? null) {
                        'negative' => $query->whereIn('conversation_id', self::ratedConversations(false)),
                        'positive' => $query
                            ->whereIn('conversation_id', self::ratedConversations(true))
                            ->whereNotIn('conversation_id', self::ratedConversations(false)),
                        'none' => $query
                            ->whereNotIn('conversation_id', self::ratedConversations(true))
                            ->whereNotIn('conversation_id', self::ratedConversations(false)),
                        default => $query,
                    }),
            ])
            ->recordActions([
                // The library travels in the link. The transcript page binds
                // the connection before it can resolve a record at all, and it
                // has no filter of its own to read that choice from.
                ViewAction::make()->url(fn ($record, $livewire) => ConversationResource::getUrl('view', [
                    'record' => $record,
                    'tenant' => $livewire->chosenLibrary(),
                ])),
            ])
            ->toolbarActions([])
            ->emptyStateHeading(__('portal.conversation.empty'))
            ->emptyStateDescription(__('portal.conversation.empty_help'));
    }

    /**
     * Conversations with a tool message matching a condition.
     *
     * A subquery rather than a join, so the outer query stays a plain listing
     * and the row-level security policy on `messages` -- which is expressed in
     * terms of `conversations` -- has the same scope to work with either way.
     */
    private static function conversationsWhere(string $condition): Builder
    {
        return ConversationMessage::query()
            ->select('conversation_id')
            ->where('role', 'tool')
            ->whereRaw($condition);
    }

    private static function hasEmptySearch(string $conversationId): bool
    {
        return ConversationMessage::query()
            ->where('conversation_id', $conversationId)
            ->where('role', 'tool')
            ->whereRaw('content LIKE \'%"records": []%\'')
            ->exists();
    }

    /**
     * What a reader made of this conversation: true, false, or nothing said.
     *
     * `bool_and` is the three states in one scalar — false if any answer was
     * marked not useful, true if every rated one was useful, and null when
     * there are no ratings at all. One query rather than two, and the null is
     * the ordinary case by a wide margin.
     *
     * **A negative outweighs a positive**, and that is a decision rather than a
     * side effect of the aggregate. Two of the seven rated conversations on the
     * log this was written against carry both. This listing exists to find the
     * ones worth opening, and a reader who was helped twice and failed once is
     * one of them.
     */
    private static function ratingOf(string $conversationId): ?bool
    {
        return MessageRating::query()
            ->whereIn(
                'message_id',
                ConversationMessage::query()
                    ->select('message_id')
                    ->where('conversation_id', $conversationId),
            )
            ->selectRaw('bool_and(useful) as verdict')
            ->value('verdict');
    }

    /**
     * Conversations carrying at least one rating of the given sign.
     *
     * A subquery for the same reason `conversationsWhere` gives: the outer
     * query stays a plain listing, and the policy on `messages` has the same
     * scope to work with either way.
     */
    private static function ratedConversations(bool $useful): Builder
    {
        return ConversationMessage::query()
            ->select('conversation_id')
            ->whereIn(
                'message_id',
                MessageRating::query()->select('message_id')->where('useful', $useful),
            );
    }

    private static function hasFailedTool(string $conversationId): bool
    {
        return ConversationMessage::query()
            ->where('conversation_id', $conversationId)
            ->where('role', 'tool')
            ->whereRaw('content LIKE \'%"error"%\'')
            ->exists();
    }
}
