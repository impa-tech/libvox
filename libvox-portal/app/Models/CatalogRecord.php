<?php

namespace App\Models;

use App\Database\DiscoveryScope;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Facades\DB;
use LogicException;
use RuntimeException;

/**
 * One record as the Discovery Index holds it — which is to say, as MARC.IA
 * can find it.
 *
 * Not the library's catalogue. The ILS is the source of truth and this is a
 * read model rebuilt from it by /libvox-sync, so a book here that is not in
 * the ILS means the index is stale, and a book in the ILS that is not here
 * means it has not been imported yet. Both are worth being able to see, and
 * neither is fixable from this screen.
 *
 * **Read-only, and enforced rather than intended.** libvox_app holds SELECT on
 * this view and nothing more, so a write would be refused by PostgreSQL; the
 * exceptions below turn that into a sentence naming the reason instead of a
 * permission error naming a view. Writing here would be editing a cache and
 * expecting an ILS to change.
 *
 * Backed by `discovery_active`, which already hides generations: a half-written
 * import is correctly invisible until it is swapped in.
 */
class CatalogRecord extends Model
{
    protected $connection = 'pgsql_discovery';

    protected $table = 'discovery_active';

    /**
     * The view's key is (tenant_id, biblio_id), and tenant_id is decided by
     * row-level security rather than by anything here. So biblio_id is the key
     * as far as this application is concerned, and it is not generated.
     */
    protected $primaryKey = 'biblio_id';

    public $incrementing = false;

    protected $keyType = 'int';

    public $timestamps = false;

    protected function casts(): array
    {
        return [
            'biblio_id' => 'integer',
            'copyright_date' => 'integer',
            'ils_timestamp' => 'datetime',
        ];
    }

    /**
     * The two indexed fields `discovery_active` deliberately does not carry.
     *
     * Migration 004 kept the view to the MCP contract — *"every column in it is
     * a CatalogRecord field"* — and marked `abstract` and `notes` as indexed at
     * weight D and **never returned**. That is right for the model: cataloguer
     * prose is noisy, and it would be spent context on every search.
     *
     * A librarian is not the model. This screen exists so she can see what
     * MARC.IA can find, and a record that matched on a word appearing only in
     * its abstract is exactly the case she cannot otherwise explain. So the two
     * are read here, one record at a time, from the table rather than the view
     * — `libvox_app` already holds SELECT on it, and row-level security scopes
     * it to her library the same way.
     *
     * The generation join is not optional: without it a half-written import
     * would answer with text from a generation nobody is being served yet.
     *
     * @return array{abstract: ?string, notes: ?string}
     */
    public function indexedText(): array
    {
        $row = DB::connection('pgsql_discovery')->selectOne(
            'SELECT b.abstract, b.notes
               FROM discovery_biblio b
               JOIN discovery_tenant t
                 ON t.tenant_id = b.tenant_id
                AND t.active_generation = b.generation
              WHERE b.biblio_id = ?',
            [$this->biblio_id],
        );

        return ['abstract' => $row->abstract ?? null, 'notes' => $row->notes ?? null];
    }

    /** @var list<string> */
    protected $hidden = ['search', 'tenant_id'];

    public function save(array $options = []): bool
    {
        throw new LogicException(self::WHY_READ_ONLY);
    }

    public function delete(): bool
    {
        throw new LogicException(self::WHY_READ_ONLY);
    }

    private const WHY_READ_ONLY =
        'The Discovery Index is derived from the ILS. Editing it here would '
        .'change what MARC.IA finds without changing what the library holds, '
        .'and the next import would silently undo it.';

    /**
     * Bind the Discovery connection to one library, and prove it can be.
     *
     * The assertion is the same one AppServiceProvider makes about the Tenant
     * DB, made here instead of on boot: a privileged role ignores row-level
     * security, so every librarian would see every library's catalogue, and
     * the screen would look perfectly correct while doing it.
     */
    public static function scopeToTenant(string $tenantId): void
    {
        $role = DB::connection('pgsql_discovery')->selectOne(
            'SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user'
        );

        throw_if($role === null || $role->rolsuper || $role->rolbypassrls, new RuntimeException(
            'LIBVOX_DISCOVERY_DB_DSN connects as a role that bypasses row-level '
            .'security. Every library would see every catalogue. Use libvox_app.'
        ));

        app(DiscoveryScope::class)->bind($tenantId);
    }
}
