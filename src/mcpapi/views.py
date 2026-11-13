import json

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import protocol


@csrf_exempt  # nechodí z formulářů; ochrana: povinný JSON Content-Type + kontrola Origin
def endpoint(request):
    if request.method != "POST":
        response = HttpResponse("Tento MCP server podporuje jen POST (Streamable HTTP bez SSE).", status=405)
        response["Allow"] = "POST"
        return response
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != f"{request.scheme}://{request.get_host()}":
        return HttpResponse("Nepovolený Origin.", status=403)
    if request.content_type != "application/json":
        return HttpResponse("Očekávám Content-Type: application/json.", status=415)
    version = request.headers.get("MCP-Protocol-Version")
    if version and version not in protocol.SUPPORTED_VERSIONS:
        return JsonResponse(protocol._error(None, protocol.INVALID_REQUEST,
                                            f"Unsupported MCP-Protocol-Version: {version}"), status=400)
    try:
        message = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse(protocol._error(None, protocol.PARSE_ERROR, "Parse error"), status=400)
    if isinstance(message, list):
        return JsonResponse(protocol._error(None, protocol.INVALID_REQUEST, "Batch requests are not supported"),
                            status=400)
    response = protocol.handle(request, message)
    if response is None:
        return HttpResponse(status=202)
    return JsonResponse(response, json_dumps_params={"ensure_ascii": False})
