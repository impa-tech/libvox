<?php

namespace App\Logging;

use Monolog\LogRecord;
use Monolog\Processor\ProcessorInterface;

/**
 * Removes LIBVOX_CRYPTO_KEY from anything on its way to a log file.
 *
 * The belt to the exception handler's braces. That handler covers the path
 * anyone thought about; this covers `Log::info($sql)` in a debugging session
 * that outlives the afternoon it was written in.
 *
 * It only knows one secret, and that is deliberate: a redactor with a list of
 * patterns invites the belief that anything not on the list is safe to log.
 * The list here is "the value that must never appear", and it is exact.
 */
class RedactSecrets implements ProcessorInterface
{
    public function __invoke(LogRecord $record): LogRecord
    {
        $key = config('libvox.crypto_key');

        if (! is_string($key) || $key === '') {
            return $record;
        }

        $scrub = fn (string $text): string => str_replace($key, '[LIBVOX_CRYPTO_KEY redacted]', $text);

        return $record->with(
            message: $scrub($record->message),
            context: $this->scrubDeep($record->context, $scrub),
        );
    }

    /**
     * @param  array<mixed>  $values
     * @return array<mixed>
     */
    private function scrubDeep(array $values, callable $scrub): array
    {
        foreach ($values as $index => $value) {
            $values[$index] = match (true) {
                is_string($value) => $scrub($value),
                is_array($value) => $this->scrubDeep($value, $scrub),
                default => $value,
            };
        }

        return $values;
    }
}
