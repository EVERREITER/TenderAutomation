"""Expose the existing Azure Functions HTTP greeting endpoint.

The module creates the FunctionApp instance discovered by the Azure Functions
host. Its entry point, func_tenderautomation(req), reads a name from the query
string or JSON request body and returns a greeting or usage guidance. This
endpoint is independent of the local tender extraction CLI.
"""
# v1
import azure.functions as func
import logging

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

@app.route(route="func_tenderautomation")
def func_tenderautomation(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a request.')

    name = req.params.get('name')
    if not name:
        try:
            req_body = req.get_json()
        except ValueError:
            pass
        else:
            name = req_body.get('name')

    if name:
        return func.HttpResponse(f"Hello, {name}. This HTTP triggered function executed successfully.")
    else:
        return func.HttpResponse(
             "This HTTP triggered function executed successfully. Pass a name in the query string or in the request body for a personalized response.",
             status_code=200
        )