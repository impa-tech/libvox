<?php

/*
 * Deliberately partial.
 *
 * Laravel resolves a translation key against the active locale and falls back
 * to the fallback locale per key, not per file -- so the rules this portal's
 * forms actually use are translated here, and anything else still answers in
 * English rather than showing a raw key.
 *
 * Translating the whole of Laravel's validation file would be several hundred
 * lines, most of them for rules no form here uses, each one a chance to
 * mistranslate something nobody will ever read. When a new rule starts firing
 * in a form, it gets a line here.
 */

return [
    'required' => 'O campo :attribute é obrigatório.',
    'email' => 'O campo :attribute precisa ser um endereço de e-mail válido.',
    'url' => 'O campo :attribute precisa ser uma URL válida.',
    'unique' => 'Este :attribute já está em uso.',
    'confirmed' => 'A confirmação do campo :attribute não confere.',
    'regex' => 'O formato do campo :attribute é inválido.',
    'string' => 'O campo :attribute precisa ser um texto.',
    'boolean' => 'O campo :attribute precisa ser verdadeiro ou falso.',
    'in' => 'O valor selecionado em :attribute é inválido.',
    'array' => 'O campo :attribute precisa ser uma lista.',

    'max' => [
        'string' => 'O campo :attribute não pode ter mais que :max caracteres.',
        'numeric' => 'O campo :attribute não pode ser maior que :max.',
        'array' => 'O campo :attribute não pode ter mais que :max itens.',
    ],

    'min' => [
        'string' => 'O campo :attribute precisa ter ao menos :min caracteres.',
        'numeric' => 'O campo :attribute precisa ser ao menos :min.',
        'array' => 'O campo :attribute precisa ter ao menos :min itens.',
    ],

    /*
     * Never add :input to a message here. It echoes the submitted value, and
     * one of the fields on these forms is a library's ILS client secret.
     */
];
