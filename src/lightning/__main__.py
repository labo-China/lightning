import argparse
import logging
import webbrowser
from .server import Server
from .interfaces import StorageView, Echo

parser = argparse.ArgumentParser()
parser.add_argument('--host', default = '0.0.0.0', help = 'host IP address')
parser.add_argument('--port', default = 80, help = 'port number to bind server', type = int)
parser.add_argument('--path', default = '.', help = 'directory to share')
parser.add_argument('--depth', default = 0, help = 'maxmium depth of direcrtories', type = int)
parser.add_argument('--strict', action = 'store_true', help = 'disable view pages and disallow exceeded symlinks')
parser.add_argument('-e', '--enable-echo', action = 'store_true', help = 'enable echo on /echo')
parser.add_argument('-v', '--verbose', action = 'store_true', help = 'print INFO messages')
parser.add_argument('-q', '--quiet', action = 'store_true', help = 'not to open web page in browsers')

args = parser.parse_args()

if args.verbose:
    logging.basicConfig(level = 'INFO')
    logging.info('Verbose enabled')

server = Server((args.host, args.port))
if args.enable_echo:
    server.bind('/echo', Echo)
if args.strict:
    conf = {'enable_view': False, 'allow_exceeded_links': False}
else:
    conf = {'enable_view': True, 'allow_exceeded_links': True}
server.bind('/', StorageView(root = args.path, depth = args.depth, **conf))

if not args.quiet:
    # assume that server will run before the browser actually starts to render the page
    # so open browser first is totally fine here because browsers usually have a long startup time
    webbrowser.open(f'http://{args.host}:{args.port}/')
server.run()
