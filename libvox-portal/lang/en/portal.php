<?php

/*
 * Every user-facing string in the panels, in both locales.
 *
 * Code, comments, migrations, column names and test names stay English; only
 * this directory is translated. A test asserts the two locales carry the same
 * keys in both directions, because the usual failure is adding a string in one
 * and finding the raw key rendered in the other.
 */

return [
    'save' => 'Save',

    'locale' => [
        'switch_to' => 'Ver em português',
    ],

    'status_bar' => [
        'signed_in_as' => 'Signed in as',
        'library' => 'Library',
    ],

    'source' => [
        'notice' => 'LibVox is free software, licensed AGPL-3.0.',
        'link' => 'Source code',
    ],

    'role' => [
        'operator' => 'LibVox operator',
        'librarian' => 'Librarian',
    ],

    'tenant' => [
        'label' => 'Library',
        'label_plural' => 'Libraries',
        'tenant_id' => 'Identifier',
        'tenant_id_help' => 'Carried by every other LibVox component and embedded in indexed records. It cannot be changed afterwards.',
        'display_name' => 'Name',
        'status_label' => 'Status',
        'locale' => 'Language',
        'contact_email' => 'Contact',
        'ils_configured' => 'Catalog',
        'ils_configured_at' => 'Catalog configured',
        'created_at' => 'Registered',
        'never' => 'Not yet',

        'section' => [
            'identity' => 'Identity',
            'identity_help' => 'The identifier is how every other component refers to this library. Choose it once.',
            'service' => 'Service',
        ],
        'status' => [
            'provisioning' => 'Provisioning',
            'active' => 'Active',
            'suspended' => 'Suspended',
        ],
    ],

    'channel' => [
        'label' => 'Channel',
        'label_plural' => 'Channels',
        'widget_key' => 'Widget key',
        'binding_key' => 'Identifier',
        'binding_key_help' => 'Generated, and public: it says which library, and authorizes nothing. What authorizes a connection is the allowed origins beside it.',
        'allowed_origins' => 'Allowed origins',
        'allowed_origins_help' => 'The pages the widget may be embedded in, scheme and host only. The widget cannot be switched on until there is at least one.',
        'no_origins' => 'None yet',
        'enabled' => 'Active',
        'enabled_help' => 'A channel that is off accepts nothing. Turning one on needs whatever credential its platform requires.',
        'enable_needs_origin' => 'List the page the widget lives on before switching it on. A key with no registered page could be embedded anywhere.',

        'section' => [
            'identity' => 'How this channel identifies the library',
            'origins' => 'Where the widget may be embedded',
            'origins_help' => 'An exact list. A page not on it cannot open a conversation, which is what keeps the key being public from mattering.',
            'state' => 'Availability',
        ],

        'type' => [
            'web' => 'Website widget',
            'telegram' => 'Telegram',
            'whatsapp' => 'WhatsApp',
            'email' => 'E-mail',
        ],

        'hint' => [
            'web' => 'Nothing to obtain: paste the key into your OPAC page and list that page below.',
            'telegram' => 'Needs a bot token from BotFather. The identifier is the bot id, not the token.',
            'whatsapp' => 'Needs a verified WhatsApp Business number and its app secret.',
            'email' => 'Needs an address delivered to LibVox, by MX record or forwarding rule.',
        ],
    ],

    'ils' => [
        'test' => 'Test the connection',
        'check' => [
            'heading' => 'Connection test',
            'ok' => 'It works. The credentials were accepted and the catalogue answered.',
            'empty' => 'The credentials were accepted, but the catalogue answered with no records at all. That is almost always the API user missing Koha\'s `catalogue` flag — and a catalogue that looks empty makes the assistant tell readers the library holds nothing.',
            'unauthorized' => 'Koha rejected the credentials. Check the id and the secret, and that the API user is active.',
            'token_refused' => 'Your credentials were accepted and Koha issued a token — then refused that same token. The id and the secret are right; this one is the server\'s. The usual cause is Koha running under CGI, where every request is a fresh process and the issued token does not survive to the next one; Koha\'s own staff interface warns about it, and `koha-plack --enable` is the fix. An expired API user, or one with no username, gives the same error.',
            'permission_denied' => 'The credentials were accepted but lack permissions. The API user needs Koha\'s `catalogue` flag.',
            'upstream_unavailable' => 'The address could not be reached. Check the URL, and that the server accepts connections from outside.',
            'upstream_error' => 'The address answered, but not with Koha\'s API. That is almost always something in front of the site — a login page, an anti-bot block — intercepting before the catalogue is reached. The credentials were never assessed.',
            'not_configured' => 'No connection has been saved yet. Fill the form above and save before testing.',
            'no_driver' => 'LibVox has no integration for that kind of system yet.',
            'core_unreachable' => 'The service that runs the test could not be reached. That is LibVox, not your server — tell whoever administers it.',
            'unknown' => 'Something went wrong on the way. The detail below is what came back.',
        ],
        'nav' => 'Catalog connection',
        'title' => 'Where this library\'s catalog lives',
        'saved' => 'Connection saved.',
        'save_failed' => 'The connection could not be saved. The reason is in the operator\'s log, deliberately not here.',

        'section' => [
            'where' => 'The catalog',
            'where_help' => 'LibVox reads this catalog and never writes to it. A read-only key is all it needs, and all it should be given.',
            'credentials' => 'API credentials',
            'credentials_help' => 'From the ILS itself. In Koha: Administration, Manage API keys, on a patron with the catalogue permission and a userid -- both are required.',
        ],

        'ils_type' => 'System',
        'base_url' => 'Address',
        'client_id' => 'Client ID',
        'client_id_help' => 'An identifier, not a secret -- the equivalent of a username. Stored as typed.',
        'client_secret' => 'Client secret',
        'client_secret_help' => 'Encrypted before it is stored, and never shown again.',
        'client_secret_stored' => 'A secret is stored, last changed :at. Leave blank to keep it.',
    ],

    'profile' => [
        'title' => 'Change password',
        'help' => 'Only you set your password. Not even the LibVox operator can read the current one — if you forget it, they set a new one rather than recovering this.',
    ],

    'user' => [
        'label' => 'Account',
        'label_plural' => 'Accounts',
        'name' => 'Name',
        'email' => 'E-mail',
        'role' => 'Role',
        'tenant' => 'Library',
        'tenant_help' => 'A librarian belongs to exactly one library and only ever sees that one.',
        'no_tenant' => 'LibVox',
        'password' => 'Password',
        'password_help' => 'Leave blank to keep the current one.',
    ],

    'conversation' => [
        'label' => 'Conversation',
        'label_plural' => 'Conversations',
        'library' => 'Library',
        'choose' => 'Choose a library to read its conversations. Nothing is listed until you do — the database returns no conversation without knowing whose it is.',
        'reading' => 'Reading :library\'s conversations.',
        'started' => 'Started',
        'last' => 'Last message',
        'channel' => 'Channel',
        'turns' => 'Questions',
        'transcript' => 'Transcript',
        'unanswered' => 'Empty search',
        'unanswered_help' => 'A search came back with no records at all. That is where the confidently wrong answer lives.',
        'failed' => 'Failure',
        'failed_help' => 'A tool could not answer.',
        'only_unanswered' => 'Only those with an empty search',
        'only_failed' => 'Only those with a failed tool',
        'rating' => 'Rating',
        'rating_help' => 'What the reader said about this conversation\'s answers. A negative outweighs a positive: it is the one that says which conversation is worth opening. Blank means nobody said anything, which is the ordinary case.',
        'rated_positive' => 'positive',
        'rated_negative' => 'negative',
        'rated_none' => 'unrated',
        'rated_useful' => 'helped',
        'rated_unhelpful' => 'did not help',
        'tool_failed' => 'did not answer',
        'truncated' => 'truncated',
        'records' => '{0} no records|{1} 1 record|[2,*] :count records',
        'empty' => 'No conversations',
        'empty_help' => 'Either this library has had no questions, or none was chosen above.',
        'see_json' => 'see JSON',
        'hide_json' => 'hide JSON',
        'expand_all' => 'open every JSON',
        'collapse_all' => 'close every JSON',
        'copy' => 'copy',
        'copied' => 'copied',
        'sent' => 'What was asked',
        'came_back' => 'What came back',
    ],

    'import' => [
        'title' => 'Importing the catalogue',
        'back' => 'Back to the catalogue',
        'queued' => 'Asked for. Waiting for the import service to pick it up.',
        'unclaimed' => 'Nothing has taken the request yet.',
        'unclaimed_help' => 'Two things do that: the import service may be down, or it may be busy importing another library — a large catalogue takes minutes, and this screen cannot see what is happening in other libraries. If it stays here, whoever administers LibVox checks like this:',
        'records_so_far' => 'records imported so far',
        'records_so_far_short' => '— :count records so far',
        'records_indexed' => 'records in the catalogue',
        'of_about' => ':percent% of roughly :total, going by the previous import',
        'no_estimate' => 'This is the library\'s first import, so there is nothing yet to compare against for an estimate.',
        'failed' => 'The import did not finish.',
        'done' => 'Import finished. The assistant can find these records now.',
    ],

    'overview' => [
        'records' => 'Records in the catalogue',
        'records_help' => 'What the assistant can find',
        'last_import' => 'Last import',
        'never' => 'never',
        'current' => 'Index is current',
        'last_attempt_failed' => 'The last attempt failed — the previous catalogue is still answering',
        'never_imported' => 'This catalogue has not been imported yet, so the assistant finds nothing',
        'unreachable' => 'The index could not be read just now',
    ],

    'catalog' => [
        'label' => 'Record',
        'label_plural' => 'Catalogue',
        'title' => 'Title',
        'author' => 'Author',
        'series' => 'Series',
        'publisher' => 'Publisher',
        'year' => 'Year',
        'item_type' => 'Type',
        'biblio_id' => 'ILS id',
        'biblio_id_help' => 'The record identifier in your own system.',
        'view' => 'View',
        'isbn' => 'ISBN',
        'abstract' => 'Abstract',
        'notes' => 'Notes',
        'not_recorded' => 'Nothing recorded',
        'section_record' => 'The record, as MARC.IA sees it',
        'section_searchable' => 'Text the search uses and a reader never sees',
        'section_searchable_help' => 'These two are indexed at the lowest weight and never returned in an answer. They are where a book that turned up in a search without the word in its title is explained.',
        'changed' => 'Changed in the ILS',
        'search' => 'Search the way a reader would…',
        'empty' => 'Nothing here yet',
        'empty_help' => 'What appears in this list is exactly what the assistant can find. If your catalogue is already in the ILS and this list is empty, the import has not run.',
        'state' => 'Index :status, updated :when.',
        'never_synced' => 'This catalogue has never been imported, so the assistant finds nothing.',
        'first_import_failed' => 'The first import did not finish, so there is no catalogue for the assistant to search yet. Fix what the error below points at and ask again.',
        'never' => 'never',
        'export' => 'Export CSV',
        'export_nothing' => 'This collection has not been imported yet, so there is nothing to export.',
        'request' => 'Refresh the index',
        'request_confirm' => 'The whole library will be read from your ILS again. That is hundreds of requests to your server, and it takes a few minutes.',
        'request_confirm_after_failure' => 'The last attempt failed :when, with this from your server: ":error". If nothing has changed since, trying again now only adds load. The whole library will be read again — hundreds of requests, a few minutes.',
        'requested' => 'Asked for.',
        'requested_help' => 'The import starts shortly. This screen shows how it is going.',
        'not_requested' => 'Nothing to do.',
        'not_requested_help' => 'One is already under way, or this library has no catalogue connection configured yet.',
        'status' => [
            'ready' => 'current',
            'building' => 'being built',
            'stale' => 'out of date',
        ],
    ],

    'widget' => [
        'nav' => 'Assistant',
        'title' => 'How MARC.IA introduces herself',
        'saved' => 'Saved.',

        'section' => [
            'persona' => 'Persona',
            'persona_help' => 'How the assistant sounds. What she is allowed to claim is not configurable: every answer comes from a catalog lookup, here as everywhere.',
            'appearance' => 'Appearance',
            'install' => 'Installation',
            'install_help' => 'Where the widget may appear, the line that puts it there, and the key that turns it on. List your OPAC page first — scheme and host only — because the widget cannot go live until at least one page is listed.',
        ],

        'preview' => 'Preview',
        'preview_help' => 'This is what a reader sees. It follows your edits, before you save.',
        'preview_unavailable' => 'The preview did not load: the assistant server did not answer. The snippet above is still correct.',
        'copy' => 'Copy',
        'copied' => 'Copied.',
        'no_channel' => 'This library has no web channel, so there is no widget key to install. Ask LibVox support to restore it.',
        'channel_off' => 'The widget is switched off, so every connection is refused. List the page it lives on above, then turn it on below.',
        'channel_enabled_help' => 'Switched off, the widget refuses every reader. It cannot be turned on before the page above is listed — the database enforces that, because a key with no registered page could be embedded anywhere.',

        'assistant_name' => 'Assistant name',
        'assistant_name_help' => 'Shows at the top of the conversation and on the button that opens it — “Chat with MARC.IA”. Rename her after installing and the button catches up from the reader’s first conversation onward; paste the installation line again to fix it from the very first visit.',
        'persona_tone' => 'Tone',
        'welcome_message' => 'Opening message',
        'welcome_message_help' => 'The first thing a reader sees. There is a suggestion below — edit it freely. Left blank, the widget opens with no greeting at all.',
        'welcome_suggestion' => 'Welcome to :library! I am :assistant, and I am here to help you find what you are looking for in the collection.',
        'your_library' => 'your library',
        'default_locale' => 'Default language',
        'theme_color' => 'Accent colour',

        'tone' => [
            'formal' => 'Formal',
            'neutral' => 'Neutral',
            'friendly' => 'Friendly',
        ],
    ],
];
