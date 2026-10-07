import os

def rename_cube():
    # 1. Update commerce.yml
    commerce_path = "/opt/seleric/mage-ai/infra/cube/model_v2/cubes/commerce.yml"
    with open(commerce_path, "r") as f:
        content = f.read()
    
    # rename cube
    content = content.replace("name: orders\n  sql_table: serve.commerce_orders", "name: fact_orders\n  sql_table: serve.commerce_orders")
    
    # replace pre-aggregation
    content = content.replace("- orders.orders\n", "- fact_orders.orders\n")
    
    with open(commerce_path, "w") as f:
        f.write(content)
        
    # 2. Update product.yml
    product_path = "/opt/seleric/mage-ai/infra/cube/model_v2/cubes/product.yml"
    with open(product_path, "r") as f:
        content = f.read()
        
    content = content.replace("- name: orders\n    relationship: many_to_one", "- name: fact_orders\n    relationship: many_to_one")
    content = content.replace("{orders}.brand_id", "{fact_orders}.brand_id")
    content = content.replace("{orders}.order_id", "{fact_orders}.order_id")
    
    with open(product_path, "w") as f:
        f.write(content)
        
    # 3. Update views.yml
    views_path = "/opt/seleric/mage-ai/infra/cube/model_v2/views/views.yml"
    with open(views_path, "r") as f:
        content = f.read()
        
    # Careful not to replace the measure name "orders" in includes
    # We replace join_paths:
    content = content.replace("join_path: orders\n", "join_path: fact_orders\n")
    content = content.replace("join_path: orders.", "join_path: fact_orders.")
    content = content.replace("join_path: order_lines.orders", "join_path: order_lines.fact_orders")
    
    with open(views_path, "w") as f:
        f.write(content)
        
    print("Renamed cube 'orders' to 'fact_orders' successfully.")

if __name__ == "__main__":
    rename_cube()
