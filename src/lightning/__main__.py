import argparse
import logging
from . import server, interfaces

parser = argparse.ArgumentParser()
parser.add_argument('--host', default = '0.0.0.0', help = 'host IP address')
parser.add_argument('--port', default = 80, help = 'port number to bind server', type = int)
parser.add_argument('--path', default = '.', help = 'directory to share')
parser.add_argument('-E', '--enable-echo', action = 'store_true', help = 'enable mo on /debug')
parser.add_argument('-V', '--verbose', action = 'store_true', help = 'print INFO messages')
args = parser.parse_args()

if args.verbose:
    logging.basicConfig(level = 'INFO')
    logging.info('Verbose enabled')

server = server.Server((args.host, args.port))
if args.enable_echo:
    server.bind('/echo', interfaces.Echo)
server.bind('/', interfaces.StorageView(args.path))
server.run()