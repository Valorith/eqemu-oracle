# Schema Extensions

Create JSON files with a `tables` array.

```json
{
  "tables": [
    {
      "id": "my_custom_table",
      "table": "my_custom_table",
      "title": "my_custom_table",
      "category": "custom",
      "columns": [
        {
          "name": "id",
          "data_type": "int",
          "description": "Primary key"
        }
      ],
      "relationships": [],
      "mode": "augment"
    }
  ]
}
```
