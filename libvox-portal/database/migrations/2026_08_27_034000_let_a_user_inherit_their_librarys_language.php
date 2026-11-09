<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;

/*
 * A person who has not chosen a language should get their library's.
 *
 * users.locale arrived NOT NULL with a default, which meant every account was
 * born having chosen pt-BR -- including a librarian at a library whose own
 * default is English. The fallback chain in SetLocale reads "the person, then
 * their library, then the browser", and with a default on the column the second
 * step could never be reached.
 *
 * Nullable is what makes "not chosen yet" expressible, which is a different
 * thing from "chose Portuguese".
 */
return new class extends Migration
{
    public function up(): void
    {
        DB::unprepared(<<<'SQL'
            ALTER TABLE users
                ALTER COLUMN locale DROP NOT NULL,
                ALTER COLUMN locale DROP DEFAULT
        SQL);

        DB::unprepared(<<<'SQL'
            COMMENT ON COLUMN users.locale IS
                'Null means the person has not chosen: use their library''s
                 default, then the browser''s preference.'
        SQL);
    }

    public function down(): void
    {
        DB::unprepared(<<<'SQL'
            ALTER TABLE users
                ALTER COLUMN locale SET DEFAULT 'pt-BR',
                ALTER COLUMN locale SET NOT NULL
        SQL);
    }
};
