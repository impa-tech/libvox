<?php

use App\Channels\Origin;

/*
 * An Origin header is compared as a string. Everything here exists so that
 * "almost equal" cannot become "allowed".
 */

test('an origin is lowercased', function () {
    expect(Origin::normalize('https://Biblioteca.FURG.br'))->toBe('https://biblioteca.furg.br');
});

test('a default port is dropped', function (string $raw, string $expected) {
    expect(Origin::normalize($raw))->toBe($expected);
})->with([
    ['https://biblioteca.furg.br:443', 'https://biblioteca.furg.br'],
    ['http://biblioteca.furg.br:80', 'http://biblioteca.furg.br'],
]);

test('a non-default port is kept', function () {
    // It is part of the origin: https://host and https://host:8443 are two
    // different origins to a browser, and must be two here.
    expect(Origin::normalize('https://biblioteca.furg.br:8443'))
        ->toBe('https://biblioteca.furg.br:8443');
});

test('a path and a trailing slash are dropped', function (string $raw) {
    expect(Origin::normalize($raw))->toBe('https://biblioteca.furg.br');
})->with([
    'https://biblioteca.furg.br/',
    'https://biblioteca.furg.br/opac/index.html',
    'https://biblioteca.furg.br/opac?q=machado',
]);

test('a scheme that is not http or https is refused', function (string $raw) {
    expect(Origin::normalize($raw))->toBeNull();
})->with([
    'ftp://biblioteca.furg.br',
    'javascript:alert(1)',
    'file:///etc/passwd',
]);

test('something that is not a url at all is refused', function (string $raw) {
    expect(Origin::normalize($raw))->toBeNull();
})->with(['biblioteca.furg.br', '', 'not a url', '//biblioteca.furg.br']);

test('a wildcard is refused rather than approximated', function () {
    // A library asking for *.furg.br is asking a real question. Answering it
    // with a pattern match would turn an allowlist into something that quietly
    // permits more than it says, so it is refused and listed as not
    // implemented.
    expect(Origin::normalize('https://*.furg.br'))->toBeNull();
});
