# -*- mode: python -*-

from PyInstaller.hooks.hookutils import collect_submodules

a = Analysis([
                'bin/grow',
             ],
             pathex=[
                '.',
                './env/lib/python2.7/site-packages/',
             ],
             hiddenimports=[
                'babel.numbers',
                'babel.plural',
                'keyring',
                'keyring.credentials',
                'keyring.backends.Gnome',
                'keyring.backends.Google',
                'keyring.backends.OS_X',
                'keyring.backends.SecretService',
                'keyring.backends.Windows',
                'keyring.backends.file',
                'keyring.backends.keyczar',
                'keyring.backends.kwallet',
                'keyring.backends.multi',
                'keyring.backends.pyfs',
                'keyring.util.XDG',
                'keyring.util.escape',
                'markdown',
                'markdown.extensions',
                'pygments.formatters',
                'pygments.lexers',
                'pygments.lexers.configs',
                'pygments.lexers.data',
                'pygments.lexers.php',
                'werkzeug',
                'werkzeug._internal',
                'werkzeug.datastructures',
                'werkzeug.debug',
                'werkzeug.exceptions',
                'werkzeug.formparser',
                'werkzeug.http',
                'werkzeug.local',
                'werkzeug.routing',
                'werkzeug.script',
                'werkzeug.security',
                'werkzeug.serving',
                'werkzeug.test',
                'werkzeug.testapp',
                'werkzeug.urls',
                'werkzeug.useragents',
                'werkzeug.utils',
                'werkzeug.wrappers',
                'werkzeug.wsgi',
             ]
             + collect_submodules('pygments')
             + collect_submodules('pygments.formatters')
             + collect_submodules('pygments.lexers'),
             hookspath=None,
             runtime_hooks=None)

a.datas += [
    ('VERSION', 'grow/VERSION', 'DATA'),
    ('server/templates/error.html', 'grow/server/templates/error.html', 'DATA'),
    ('data/cacerts.txt', 'grow/data/cacerts.txt', 'DATA'),
]

pyz = PYZ(a.pure,
          name='growsdk')

exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          name='grow',
          debug=False,
          strip=None,
          upx=True,
          console=True)
