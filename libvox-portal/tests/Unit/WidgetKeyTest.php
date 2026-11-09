<?php

use App\Channels\WidgetKey;

test('a widget key is prefixed so it is recognizable in a log', function () {
    expect(WidgetKey::mint())->toStartWith('wk_');
});

test('a widget key is not guessable', function () {
    // Public, not secret: it identifies a library and authorizes nothing. But
    // guessing one is the first half of impersonating a library's widget, and
    // there is no reason to make that half free.
    $keys = collect(range(1, 50))->map(fn () => WidgetKey::mint());

    expect($keys->unique())->toHaveCount(50)
        ->and(strlen($keys->first()))->toBeGreaterThan(32);
});

test('a widget key is url and header safe', function () {
    // It travels in a script tag and in a WebSocket handshake, so base64's
    // + and / would need escaping in both.
    expect(WidgetKey::mint())->toMatch('/^wk_[A-Za-z0-9_-]+$/');
});

test('something that is not a widget key is not mistaken for one', function (string $candidate) {
    expect(WidgetKey::looksLikeOne($candidate))->toBeFalse();
})->with(['', 'wk_', 'wk_short', 'not-a-key', 'WK_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345']);
