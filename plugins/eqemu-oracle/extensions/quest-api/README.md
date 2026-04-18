# Quest API Extensions

Create JSON files with a `records` array.

`_example.json` in this folder is ignored by the loader and can be copied as a starting point.

```json
{
  "records": [
    {
      "id": "perl:method:quest:my-custom-method:abc123",
      "language": "perl",
      "kind": "method",
      "container": "quest",
      "name": "MyCustomMethod",
      "signature": "MyCustomMethod()",
      "params": [],
      "return_type": "void",
      "categories": ["Custom"],
      "mode": "augment"
    }
  ]
}
```
