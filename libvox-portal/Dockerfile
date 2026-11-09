# PHP dependencies, alone, because two later stages need them.
#
# Composer runs once. The assets stage needs `vendor/` -- the panel theme does
# `@import '.../vendor/filament/filament/resources/css/theme.css'`, and without
# it Tailwind fails with "Can't resolve", which is how this arrangement was
# arrived at rather than guessed.
FROM dunglas/frankenphp:php8.5 AS deps
WORKDIR /app

# pdo_pgsql, not pdo_mysql: this module is made of row-level security, pgcrypto,
# jsonb and SECURITY DEFINER functions, none of which MariaDB has. Installed
# here rather than later because composer refuses to resolve without intl and
# zip -- filament/support requires them.
RUN install-php-extensions pdo_pgsql intl zip opcache

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer
COPY composer.json composer.lock ./
RUN composer install --no-dev --no-scripts --no-autoloader --no-interaction --prefer-dist

# The stylesheet, compiled before PHP is in the picture.
#
# **A build step that runs anywhere but here is one that gets forgotten**, and
# this one fails in the worst way: `->viteTheme()` points the panel at a
# manifest, and without it the panel loses its stylesheet entirely rather than
# one element losing a class. `/public/build` is in .dockerignore for the same
# reason -- a `npm run build` on someone's laptop must not be able to reach the
# image through `COPY . .` and make a stale bundle look like a working one.
#
# Needs network: `bunny('Instrument Sans')` in vite.config.js downloads the font
# files during the build.
#
# `app/` and `resources/` are copied because Tailwind v4 has no config file --
# it finds class names by scanning the paths named in theme.css, and a directory
# missing here is a class silently purged.
FROM node:22-slim AS assets
WORKDIR /app
COPY --from=deps /app/vendor ./vendor
COPY package.json package-lock.json vite.config.js ./
RUN npm ci
COPY resources ./resources
COPY app ./app
RUN npm run build

# FrankenPHP: one process that serves HTTP directly.
#
# webgenium/guests builds on php:8.5-fpm, which does not serve HTTP by itself --
# it needs an nginx beside it and something to supervise the pair. Every other
# container in this repository is a single process with one ENTRYPOINT, and this
# is the base image that lets this one be the same. Same PHP version as guests.
# Built on `deps` rather than on the base image again: the extensions, composer
# and vendor/ are already there, so nothing is installed or resolved twice.
FROM deps

COPY . .
COPY --from=assets /app/public/build ./public/build
RUN composer dump-autoload --optimize --classmap-authoritative --no-interaction \
 && php artisan filament:assets

# Runs as a non-root user: this container holds LIBVOX_CRYPTO_KEY and a database
# password in its environment, and a PHP application's dependencies execute with
# the reach of a shell. Being unable to write outside its own tree is the
# cheapest limit available on what a compromised one can do.
#
# Port 8080 rather than 80, so it needs no capability to bind a privileged port
# either.
RUN useradd --create-home --uid 10001 libvox \
 && chown -R libvox:libvox /app/storage /app/bootstrap/cache
USER libvox

EXPOSE 8080

# No ENTRYPOINT of our own: the base image's already execs whatever it is given,
# which is what keeps `docker compose run --rm portal php artisan migrate`
# working. An ENTRYPOINT of ["frankenphp"] turns that into
# `frankenphp php artisan migrate`, and the error names the wrong problem.
#
# php-server is the development server. A production deployment runs
# `frankenphp run` against a Caddyfile, with TLS terminated somewhere that has
# a certificate.
CMD ["frankenphp", "php-server", "--listen", ":8080", "--root", "/app/public"]
