<?php

namespace App\Filament\Admin\Resources\Conversations;

use App\Filament\Admin\Resources\Conversations\Pages\ListConversations;
use App\Filament\Admin\Resources\Conversations\Pages\ViewConversation;
use App\Filament\Admin\Resources\Conversations\Tables\ConversationsTable;
use App\Models\Conversation;
use BackedEnum;
use Filament\Resources\Resource;
use Filament\Support\Icons\Heroicon;
use Filament\Tables\Table;

/**
 * What patrons asked, and what MARC.IA answered.
 *
 * A debugging and quality tool, and the question it exists to answer is *is she
 * answering correctly?* — which cannot be judged without reading the answers.
 * That is why it is here and not in a library's own panel.
 *
 * **One library at a time, and the database is what enforces it.** Both tables
 * carry FORCE ROW LEVEL SECURITY, and this panel binds no tenant of its own, so
 * nothing is listed until a library is chosen. The alternative — a connection
 * that ignores the policy — would put a credential reading every library's
 * conversations inside the application that serves requests, which is what
 * AppServiceProvider refuses to do for the Tenant DB and for the same reason.
 *
 * Nothing here identifies a person, because nothing in the schema does.
 */
class ConversationResource extends Resource
{
    protected static ?string $model = Conversation::class;

    protected static string|BackedEnum|null $navigationIcon = Heroicon::OutlinedChatBubbleLeftEllipsis;

    protected static ?int $navigationSort = 30;

    public static function table(Table $table): Table
    {
        return ConversationsTable::configure($table);
    }

    public static function getModelLabel(): string
    {
        return __('portal.conversation.label');
    }

    public static function getPluralModelLabel(): string
    {
        return __('portal.conversation.label_plural');
    }

    public static function getNavigationLabel(): string
    {
        return __('portal.conversation.label_plural');
    }

    /** Written by /libvox-core; nothing here creates or edits one. */
    public static function canCreate(): bool
    {
        return false;
    }

    public static function getPages(): array
    {
        return [
            'index' => ListConversations::route('/'),
            'view' => ViewConversation::route('/{record}'),
        ];
    }
}
