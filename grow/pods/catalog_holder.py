from . import catalogs
from . import importers
from babel import util as babel_util
from babel.messages import catalog
from babel.messages import catalog as babel_catalog
from babel.messages import extract
from babel.messages import pofile
from grow.common import utils
from grow.pods import messages
import click
import collections
import os
import StringIO
import tokenize


_TRANSLATABLE_EXTENSIONS = (
    '.html',
    '.md',
    '.yaml',
    '.yml',
)


class Error(Exception):
    pass


class UsageError(Error, click.UsageError):
    pass


class Catalogs(object):
    root = '/translations'

    def __init__(self, pod, template_path=None):
        self.pod = pod
        if template_path:
            self.template_path = template_path
        else:
            self.template_path = os.path.join(Catalogs.root, 'messages.pot')

    def get(self, locale, basename='messages.po', dir_path=None):
        return catalogs.Catalog(basename, locale, pod=self.pod, dir_path=dir_path)

    def get_template(self, basename='messages.pot'):
        return catalogs.Catalog(basename, None, pod=self.pod)

    def list_locales(self):
        locales = set()
        for path in self.pod.list_dir(Catalogs.root):
            parts = path.split('/')
            if len(parts) > 2:
                locales.add(parts[1])
        return list(locales)

    def validate_locales(self, locales):
        for locale in locales:
            if '_' in locale:
                parts = locale.split('_')
                territory = parts[-1]
                if territory != territory.upper():
                    parts[-1] = territory.upper()
                    correct_locale = '_'.join(parts)
                    text = 'WARNING: Translation directories are case sensitive (move {} -> {}).'
                    self.pod.logger.warning(text.format(locale, correct_locale))

    def __iter__(self):
        for locale in self.list_locales():
            yield self.get(locale)

    def __len__(self):
        return len([catalog for catalog in self])

    def compile(self, force=False):
        locales = self.list_locales()
        self.validate_locales(locales)
        for locale in locales:
            catalog = self.get(locale)
            if not catalog.exists:
                self.pod.logger.info('Does not exist: {}'.format(catalog))
                continue
            if force or catalog.needs_compilation:
                catalog.compile()

    def to_message(self):
        message = messages.CatalogsMessage()
        message.catalogs = []
        for locale in self.list_locales():
            catalog = self.get(locale)
            message.catalogs.append(catalog.to_message())
        return message

    def init(self, locales, include_header=False):
        for locale in locales:
            catalog = self.get(locale)
            catalog.init(template_path=self.template_path,
                         include_header=include_header)

    def update(self, locales, use_fuzzy_matching=False, include_header=False):
        for locale in locales:
            catalog = self.get(locale)
            self.pod.logger.info('Updating: {}'.format(locale))
            catalog.update(template_path=self.template_path,
                           use_fuzzy_matching=use_fuzzy_matching,
                           include_header=include_header)

    def import_translations(self, path=None, locale=None, content=None):
        importer = importers.Importer(self.pod)
        if path:
            importer.import_path(path, locale=locale)
        if content:
            importer.import_content(content=content, locale=locale)

    def extract(self, include_obsolete=False, localized=False, paths=None,
                include_header=False, locales=None, use_fuzzy_matching=False):
        env = self.pod.create_template_env()

        # {
        #    locale1: locale1_catalog,
        #    locale2: locale2_catalog,
        #    ...
        # }
        # This is built up as we extract
        localized_catalogs = {}
        unlocalized_catalog = catalogs.Catalog()  # for localized=False case

        comment_tags = [
            ':',
        ]
        options = {
            'extensions': ','.join(env.extensions.keys()),
            'silent': 'false',
        }

        def _add_to_catalog(message, locales):
            # Add to all relevant catalogs
            for locale in locales:
                if locale not in localized_catalogs:
                    # Start with a new catalog so we can track what's obsolete:
                    # we'll merge it with existing translations later.
                    # *NOT* setting `locale` kwarg here b/c that will load existing
                    # translations.
                    localized_catalogs[locale] = catalogs.Catalog(pod=self.pod)
                localized_catalogs[locale][message.id] = message
            unlocalized_catalog[message.id] = message

        def _handle_field(path, locales, msgid, key, node):
            if (not key
                    or not key.endswith('@')
                    or not isinstance(msgid, basestring)):
                return
            # Support gettext "extracted comments" on tagged fields:
            #   field@: Message.
            #   field@#: Extracted comment for field@.
            auto_comments = []
            if isinstance(node, dict):
                auto_comment = node.get('{}#'.format(key))
                if auto_comment:
                    auto_comments.append(auto_comment)
            message = catalog.Message(
                msgid,
                None,
                auto_comments=auto_comments,
                # TODO: line numbers
                locations=[(path, 0)])

            _add_to_catalog(message, locales)

        def _babel_extract(fp, locales, path):
            try:
                all_parts = extract.extract(
                    'jinja2.ext.babel_extract',
                    fp,
                    options=options,
                    comment_tags=comment_tags)
                for parts in all_parts:
                    lineno, msgid, comments, context = parts
                    message = catalog.Message(
                        msgid,
                        None,
                        auto_comments=comments,
                        locations=[(path, lineno)])
                    _add_to_catalog(message, locales)
            except tokenize.TokenError:
                self.pod.logger.error('Problem extracting body: {}'.format(path))
                raise

        # Extract from collections in /content/:
        # Strings only extracted for relevant locales, determined by locale
        # scope (pod > collection > document > document part)
        last_pod_path = None
        for collection in self.pod.list_collections():
            for doc in collection.list_docs(include_hidden=True):
                if doc.pod_path != last_pod_path:
                    self.pod.logger.info(
                        'Extracting: {} ({} locale{})'.format(
                            doc.pod_path,
                            len(doc.locales),
                            's' if len(doc.locales) > 1 else '',
                        )
                    )
                    last_pod_path = doc.pod_path

                # If doc.locale is set, this is a doc part: only extract for
                # its own locales (not those of base doc).
                if doc.locale:
                    doc_locales = [doc.locale]
                # If not is set, this is a base doc (1st or only part): extract
                # for all locales declared for this doc
                elif doc.locales:
                    doc_locales = doc.locales
                # Otherwise only include in template (--no-localized)
                else:
                    doc_locales = [None]

                # Extract yaml fields: `foo@: Extract me`
                # ("tagged" = prior to stripping `@` suffix from field names)
                tagged_fields = doc.get_tagged_fields()
                utils.walk(tagged_fields,
                           lambda *args: _handle_field(doc.pod_path, doc_locales, *args))

                # Extract body: {{_('Extract me')}}
                _babel_extract(StringIO.StringIO(doc.body.encode('utf-8')), doc_locales, doc.pod_path)

            # Extract from CSVs for this collection's locales
            for filepath in self.pod.list_dir(collection.pod_path):
                if filepath.endswith('.csv'):
                    pod_path = os.path.join(collection.pod_path, filepath.lstrip('/'))
                    self.pod.logger.info('Extracting: {}'.format(pod_path))
                    rows = self.pod.read_csv(pod_path)
                    for i, row in enumerate(rows):
                        for key, msgid in row.iteritems():
                            _handle_field(pod_path, collection.locales, msgid, key, row)

        # Extract from root of /content/:
        for path in self.pod.list_dir('/content/', recursive=False):
            if path.endswith('.yaml') or path.endswith('.yml'):
                pod_path = os.path.join('/content/', path)
                self.pod.logger.info('Extracting: {}'.format(pod_path))
                utils.walk(
                    self.pod.get_doc(pod_path).get_tagged_fields(),
                    lambda *args: _handle_field(pod_path, self.pod.list_locales(), *args)
                )

        # Extract from /views/:
        # Not discriminating by file extension, because people use all sorts
        # (htm, html, tpl, dtml, jtml, ...)
        for path in self.pod.list_dir('/views/'):
            pod_path = os.path.join('/views/', path)
            self.pod.logger.info('Extracting: {}'.format(pod_path))
            with self.pod.open_file(pod_path) as f:
                _babel_extract(f, self.pod.list_locales(), pod_path)

        # Extract from podspec.yaml:
        self.pod.logger.info('Extracting: podspec.yaml')
        utils.walk(
            self.pod.get_podspec().get_config(),
            lambda *args: _handle_field('/podspec.yaml', self.pod.list_locales(), *args)
        )

        # Save it out: behavior depends on --localized and --locale flags
        if localized:
            # Save each localized catalog
            for locale, new_catalog in localized_catalogs.items():
                # Skip if `locales` defined but doesn't include this locale
                if locales and locale not in locales:
                    continue
                existing_catalog = self.get(locale)
                existing_catalog.update_using_catalog(
                    new_catalog,
                    include_obsolete=include_obsolete)
                existing_catalog.save(include_header=include_header)
                missing = existing_catalog.list_untranslated()
                num_messages = len(existing_catalog)
                self.pod.logger.info(
                    'Saved: /{path} ({num_translated}/{num_messages})'.format(
                        path=existing_catalog.pod_path,
                        num_translated=num_messages - len(missing),
                        num_messages=num_messages)
                    )
        else:
            # --localized omitted / --no-localized
            template_catalog = self.get_template()
            template_catalog.load()
            template_catalog.update_using_catalog(
                unlocalized_catalog,
                include_obsolete=include_obsolete)
            template_catalog.save(include_header=include_header)
            text = 'Saved: {} ({} messages)'
            self.pod.logger.info(
                text.format(template_catalog.pod_path, len(template_catalog))
            )
            return template_catalog

    def write_template(self, template_path, catalog, include_obsolete=False,
                       include_header=False):
        template_file = self.pod.open_file(template_path, mode='w')
        catalogs.Catalog.set_header_comment(self.pod, catalog)
        pofile.write_po(
            template_file, catalog, width=80, omit_header=(not include_header),
            sort_output=True, sort_by_file=True,
            ignore_obsolete=(not include_obsolete))
        text = 'Saved: {} ({} messages)'
        self.pod.logger.info(text.format(template_path, len(catalog)))
        template_file.close()
        return catalog

    def filter(self, out_path=None, out_dir=None,
               include_obsolete=True, localized=False,
               paths=None, include_header=None, locales=None):
        if localized and out_dir is None:
            raise UsageError('Must specify --out_dir when using --localized in '
                             'order to generate localized catalogs.')
        if not localized and out_path is None:
            raise UsageError('Must specify -o when not using --localized.')
        filtered_catalogs = []
        messages_to_locales = collections.defaultdict(list)
        for locale in locales:
            locale_catalog = self.get(locale)
            missing_messages = locale_catalog.list_untranslated(paths=paths)
            num_missing = len(missing_messages)
            num_total = len(locale_catalog)
            for message in missing_messages:
                messages_to_locales[message].append(locale_catalog.locale)
            # Generate localized catalogs.
            if localized:
                filtered_catalog = self.get(locale, dir_path=out_dir)
                for message in missing_messages:
                    filtered_catalog[message.id] = message
                if len(filtered_catalog):
                    text = 'Saving: {} ({} missing of {})'
                    text = text.format(filtered_catalog.pod_path, num_missing,
                                       num_total)
                    self.pod.logger.info(text)
                    filtered_catalog.save(include_header=include_header)
                else:
                    text = 'Skipping: {} (0 missing of {})'
                    text = text.format(filtered_catalog.pod_path, num_total)
                    self.pod.logger.info(text)
                filtered_catalogs.append(filtered_catalog)
        if localized:
            return filtered_catalogs
        # Generate a single catalog template.
        self.pod.create_file(out_path, None)
        babel_catalog = pofile.read_po(self.pod.open_file(out_path))
        for message in messages_to_locales.keys():
            babel_catalog[message.id] = message
        self.write_template(out_path, babel_catalog,
                            include_header=include_header)
        return [babel_catalog]
