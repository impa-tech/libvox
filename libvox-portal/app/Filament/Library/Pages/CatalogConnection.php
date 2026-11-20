<?php

namespace App\Filament\Library\Pages;

use App\Enums\IlsType;
use App\Ils\CredentialStorageFailed;
use App\Ils\IlsConnection;
use App\Ils\IlsConnectionCheck;
use App\Ils\IlsCredentialStore;
use BackedEnum;
use Filament\Actions\Action;
use Filament\Facades\Filament;
use Filament\Forms\Components\Select;
use Filament\Forms\Components\TextInput;
use Filament\Forms\Concerns\InteractsWithForms;
use Filament\Forms\Contracts\HasForms;
use Filament\Notifications\Notification;
use Filament\Pages\Page;
use Filament\Schemas\Components\Section;
use Filament\Schemas\Components\Utilities\Get;
use Filament\Schemas\Schema;
use Filament\Support\Icons\Heroicon;
use Illuminate\Support\Facades\Lang;

/**
 * Where this library's catalog is, and how LibVox authenticates against it.
 *
 * Koha signs in with an API key and Evergreen with a staff username and
 * password. They are kept in the same two columns, so the form changes what it
 * calls the fields, and its help text, and nothing about how they are stored.
 *
 * The secret is write-only, and that is a property of the whole system rather
 * than of this form: IlsCredentialStore has no read path, so there is nothing
 * for the field to be hydrated from. On edit it renders empty and says so;
 * left blank, it keeps whatever is stored.
 */
class CatalogConnection extends Page implements HasForms
{
    use InteractsWithForms;

    protected string $view = 'filament.library.pages.catalog-connection';

    protected static string|BackedEnum|null $navigationIcon = Heroicon::OutlinedKey;

    /** @var array<string, mixed>|null */
    public ?array $data = [];

    public ?string $secretStoredAt = null;

    public static function getNavigationLabel(): string
    {
        return __('portal.ils.nav');
    }

    public function getTitle(): string
    {
        return __('portal.ils.title');
    }

    public function mount(): void
    {
        $summary = $this->store()->describe($this->tenantId());

        $this->secretStoredAt = $summary?->secretUpdatedAt;

        // Everything except the secret. There is no fourth key to fill in here,
        // because there is no method that could produce one.
        $this->form->fill([
            'ils_type' => $summary?->connection->ilsType->value ?? IlsType::Koha->value,
            'base_url' => $summary?->connection->baseUrl,
            'client_id' => $summary?->connection->clientId,
            'opac_url' => $summary?->connection->opacUrl,
        ]);
    }

    public function form(Schema $schema): Schema
    {
        return $schema
            ->components([
                Section::make(__('portal.ils.section.where'))
                    ->description(__('portal.ils.section.where_help'))
                    ->schema([
                        Select::make('ils_type')
                            ->label(__('portal.ils.ils_type'))
                            ->options(collect(IlsType::cases())
                                ->mapWithKeys(fn (IlsType $t) => [$t->value => $t->label()])
                                ->all())
                            // Live, so the labels below follow the choice
                            // before anything is saved.
                            ->live()
                            ->required(),

                        TextInput::make('base_url')
                            ->label(__('portal.ils.base_url'))
                            ->helperText(fn (Get $get): ?string => $this->forType('base_url_help', $get('ils_type')))
                            ->url()
                            ->required()
                            ->maxLength(255),

                        // Optional: without it MARC.IA names a book and links
                        // nowhere, which is what she did before it existed.
                        TextInput::make('opac_url')
                            ->label(__('portal.ils.opac_url'))
                            ->helperText(fn (Get $get): string => $this->forType('opac_url_help', $get('ils_type')))
                            ->url()
                            ->rule('regex:/^https?:\/\//i')
                            ->maxLength(255),
                    ]),

                Section::make(fn (Get $get): string => $this->forType('section.credentials', $get('ils_type')))
                    ->description(fn (Get $get): string => $this->forType('section.credentials_help', $get('ils_type')))
                    ->schema([
                        TextInput::make('client_id')
                            ->label(fn (Get $get): string => $this->forType('client_id', $get('ils_type')))
                            ->helperText(fn (Get $get): string => $this->forType('client_id_help', $get('ils_type')))
                            ->required()
                            ->maxLength(255),

                        TextInput::make('client_secret')
                            ->label(fn (Get $get): string => $this->forType('client_secret', $get('ils_type')))
                            ->password()
                            ->revealable(false)
                            ->autocomplete('new-password')
                            ->extraInputAttributes(['data-1p-ignore' => 'true'])
                            // Never hydrated: there is no read path to hydrate
                            // it from. Blank on edit means "keep the stored one".
                            ->dehydrated(fn (?string $state): bool => filled($state))
                            ->required(fn (): bool => $this->secretStoredAt === null)
                            ->helperText(fn (Get $get): string => $this->secretStoredAt === null
                                ? $this->forType('client_secret_help', $get('ils_type'))
                                : $this->forType('client_secret_stored', $get('ils_type'), ['at' => $this->secretStoredAt]))
                            ->maxLength(512),
                    ]),
            ])
            ->statePath('data');
    }

    public function save(): void
    {
        $state = $this->form->getState();

        $connection = new IlsConnection(
            tenantId: $this->tenantId(),
            ilsType: IlsType::from($state['ils_type']),
            baseUrl: $state['base_url'],
            clientId: $state['client_id'],
            opacUrl: $state['opac_url'] ?? null,
        );

        try {
            // The field is write-only, so it renders blank on every edit and a
            // librarian changing only the URL submits no secret. Passing an
            // empty string here would encrypt it and overwrite a working
            // credential with nothing -- which is why the two cases are two
            // different calls rather than one nullable argument.
            filled($state['client_secret'] ?? null)
                ? $this->store()->put($connection, $state['client_secret'])
                : $this->store()->updateConnection($connection);
        } catch (CredentialStorageFailed) {
            Notification::make()->danger()->title(__('portal.ils.save_failed'))->send();

            return;
        }

        $this->mount();

        Notification::make()->success()->title(__('portal.ils.saved'))->send();
    }

    /** @return array<Action> */
    protected function getFormActions(): array
    {
        return [
            Action::make('save')->label(__('portal.save'))->submit('save'),
            Action::make('testConnection')
                ->label(__('portal.ils.test'))
                ->color('gray')
                ->action('testConnection'),
        ];
    }

    /** The last check's answer, or null before one has been asked for. */
    public ?array $check = null;

    /**
     * Try the stored credentials against the library's own server.
     *
     * The answer is worth more than yes or no: unreachable, rejected and
     * unpermitted have three different fixes, and "it did not work" sends a
     * librarian to check all three.
     */
    public function testConnection(): void
    {
        $this->check = app(IlsConnectionCheck::class)->run($this->tenantId());

        $this->dispatch('open-modal', id: 'ils-check');
    }

    /** The sentence for this outcome, keyed by the driver's own error code. */
    public function checkMessage(): string
    {
        if ($this->check === null) {
            return '';
        }

        if ($this->check['ok']) {
            return $this->check['records_seen'] > 0
                ? __('portal.ils.check.ok')
                // Authenticating and reading nothing is the failure this
                // system is most careful about: it is how a library ends up
                // telling readers it holds no books.
                : __('portal.ils.check.empty');
        }

        $known = [
            'unauthorized', 'token_refused', 'permission_denied', 'upstream_unavailable',
            'upstream_error', 'not_configured', 'no_driver', 'core_unreachable',
        ];

        return $this->forType('check.'.(
            in_array($this->check['problem'], $known, true)
                ? $this->check['problem']
                : 'unknown'
        ), $this->data['ils_type'] ?? null);
    }

    /**
     * A string of this page, in the words of the chosen ILS.
     *
     * Most strings say the same thing for every system. The ones that name a
     * Koha permission or a Koha screen have an Evergreen counterpart under
     * `portal.ils.evergreen.*`; anything without one falls back to the shared
     * string, so a new key never renders as its own name.
     */
    public function forType(string $key, mixed $type, array $replace = []): string
    {
        $value = $type instanceof IlsType ? $type->value : $type;

        if (is_string($value) && $value !== IlsType::Koha->value
            && Lang::has("portal.ils.{$value}.{$key}")) {
            return __("portal.ils.{$value}.{$key}", $replace);
        }

        return Lang::has("portal.ils.{$key}") ? __("portal.ils.{$key}", $replace) : '';
    }

    private function tenantId(): string
    {
        return Filament::auth()->user()->tenant_id;
    }

    private function store(): IlsCredentialStore
    {
        return app(IlsCredentialStore::class);
    }
}
