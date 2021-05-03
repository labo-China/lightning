from Structs import Request, Interface, Response


def default(request: Request):
    return Response(content = repr(request))


DefaultInterface = Interface(default, Request)
