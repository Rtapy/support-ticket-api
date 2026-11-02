from django.contrib.staticfiles import finders
from django.test import SimpleTestCase
from django.urls import reverse
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema


class OpenAPISchemaTests(SimpleTestCase):
    def test_docs_are_public_local_assets_exist_and_ticket_api_still_requires_token(self):
        response = self.client.get(reverse('swagger-ui'))
        self.assertContains(response, '/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-bundle.js')
        self.assertContains(response, '/api/schema/')
        self.assertNotContains(response, 'cdn.jsdelivr.net')
        self.assertIsNotNone(finders.find('drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-bundle.js'))
        response = self.client.get(reverse('schema'), HTTP_ACCEPT='application/vnd.oai.openapi+json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('/api/tickets/', response.data['paths'])
        self.assertEqual(self.client.get(reverse('ticket-list')).status_code, 401)

    def test_complete_schema_matches_requests_authentication_and_cursor_responses(self):
        # SimpleTestCase forbids DB access: schema introspection must not read user data.
        schema = SchemaGenerator().get_schema(request=None, public=True)
        validate_schema(schema)
        paths = schema['paths']
        expected = {
            '/api/tickets/': {'get', 'post'},
            '/api/tickets/{id}/': {'get'},
            '/api/tickets/{id}/assign/': {'post'},
            '/api/tickets/{id}/status/': {'post'},
            '/api/tickets/{id}/close/': {'post'},
            '/api/tickets/{id}/reopen/': {'post'},
            '/api/tickets/{id}/history/': {'get'},
            '/api/tickets/{id}/messages/': {'get', 'post'},
        }
        self.assertEqual({path: set(methods) for path, methods in paths.items()}, expected)
        operations = [operation for methods in paths.values() for operation in methods.values()]
        self.assertEqual(len({operation['operationId'] for operation in operations}), 10)
        for operation in operations:
            self.assertEqual(operation['security'], [{'tokenAuth': []}])
            self.assertIn('401', operation['responses'])
            self.assertTrue(operation['description'])
        token = schema['components']['securitySchemes']['tokenAuth']
        self.assertEqual((token['type'], token['in'], token['name']), ('apiKey', 'header', 'Authorization'))
        self.assertIn('Token', token['description'])

        def component(reference):
            return schema['components']['schemas'][reference['$ref'].split('/')[-1]]

        for path in ('/api/tickets/', '/api/tickets/{id}/messages/', '/api/tickets/{id}/history/'):
            operation = paths[path]['get']
            self.assertIn('cursor', [parameter['name'] for parameter in operation['parameters']])
            body = component(operation['responses']['200']['content']['application/json']['schema'])
            self.assertEqual(set(body['properties']), {'next', 'previous', 'results'})
        status_parameter = next(parameter for parameter in paths['/api/tickets/']['get']['parameters'] if parameter['name'] == 'status')
        self.assertEqual(set(status_parameter['schema']['enum']), {'OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED'})
        requests = {
            '/api/tickets/': ({'title', 'description', 'priority'}, {'title', 'description'}),
            '/api/tickets/{id}/status/': ({'status'}, {'status'}),
            '/api/tickets/{id}/assign/': ({'assignee_id'}, {'assignee_id'}),
            '/api/tickets/{id}/messages/': ({'content', 'new_status'}, {'content'}),
        }
        for path, (fields, required) in requests.items():
            operation = paths[path]['post']
            body = component(operation['requestBody']['content']['application/json']['schema'])
            self.assertEqual(set(body['properties']), fields)
            self.assertEqual(set(body['required']), required)
            self.assertNotIn('cursor', [parameter['name'] for parameter in operation.get('parameters', [])])
            if path.endswith('/assign/'):
                self.assertTrue(body['properties']['assignee_id']['nullable'])
            if path.endswith('/messages/'):
                self.assertFalse(body['properties']['new_status'].get('nullable', False))
        creation = component(paths['/api/tickets/']['post']['requestBody']['content']['application/json']['schema'])
        self.assertEqual(creation['properties']['priority']['default'], 'MEDIUM')
        for action in ('close', 'reopen'):
            operation = paths[f'/api/tickets/{{id}}/{action}/']['post']
            self.assertNotIn('requestBody', operation)
        for methods in paths.values():
            if 'post' in methods:
                operation = methods['post']
                success = operation['responses'].get('201', operation['responses'].get('200'))
                body = component(success['content']['application/json']['schema'])
                self.assertIn('id', body['properties'])
                self.assertNotIn('results', body['properties'])
