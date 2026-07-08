import os

def create_directory_structure():
    base_dir = "Titan"
    subdirs = [
        "core",
        "market",
        "strategies",
        "execution",
        "risk",
        "portfolio",
        "learning",
        "research",
        "storage",
        "dashboard",
        "tests",
        "config",
        "logs",
        "models"
    ]
    
    # Create base dir
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)
        print(f"Created {base_dir}")
        
    with open(os.path.join(base_dir, "__init__.py"), "w") as f:
        f.write("# Titan package\n")
        
    for sd in subdirs:
        path = os.path.join(base_dir, sd)
        if not os.path.exists(path):
            os.makedirs(path)
            print(f"Created {path}")
        
        # Don't create __init__.py in logs since it's just for log files
        if sd != "logs":
            init_file = os.path.join(path, "__init__.py")
            with open(init_file, "w") as f:
                f.write(f"# Titan {sd} module\n")
                
    print("Directory setup completed successfully.")

if __name__ == "__main__":
    create_directory_structure()
