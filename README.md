# MongoManager Project  
A full stack MongoDB management and learning environment built with Python, FastAPI, and Swagger UI.

## Overview
This project began as a simple CRUD module written for SNHU’s CS340 course. Over time it evolved into a complete MongoDB management tool, then expanded again during the CS499 capstone into a full stack environment that includes:

- A web based interface for managing MongoDB databases  
- CRUD operations on documents and collections  
- Index and aggregation support  
- Backup and restore functionality  
- A SQL to MongoDB translation engine  
- A teaching and experimentation environment for users of all experience levels  

The goal is to make MongoDB easier to explore, easier to understand, and easier to work with, especially for users who are more familiar with SQL.

## Project History
This project has gone through several stages of development.

### CS340: The Beginning
The original artifact was a small Python class that performed basic CRUD operations on a MongoDB database. It was simple, functional, and a good foundation.

### Professional Use
After CS340, I continued using MongoDB in my career and realized that many operations could be made easier for casual users. That experience shaped the direction of this project.

### CS499 Milestone 2
The CRUD module was expanded into a full FastAPI application with a Swagger UI interface. This made the database operations interactive and easy to test.

### CS499 Milestone 3
A SQL to MongoDB translation engine was added. This enhancement allows users to enter SQL commands and see how they translate into MongoDB operations.

### CS499 Milestone 4
The database management layer was completed. This included authentication, database and collection operations, document handling, indexing, aggregations, backups, and integration with the SQL helper.

The result is a flexible, functional, and educational tool for working with MongoDB.

## Features
### Database Management
- Authenticate to a MongoDB instance  
- List, create, select, and drop databases  

### Collection Management
- List, create, rename, set, and drop collections  

### Document Operations
- Insert, read, update, and delete documents  
- Insert multiple documents at once  
- Insert documents from JSON files  
- Query with filters  
- Return results in structured JSON  

### Indexes and Aggregations
- Create and list indexes  
- Run aggregation pipelines  

### Backup and Restore
- Export collections to JSON  
- Restore collections from JSON  

### SQL Helper
- Translate SQL commands (SELECT, INSERT, UPDATE, DELETE) into MongoDB operations  
- Useful for teaching, learning, and comparing database paradigms  

## Requirements
You will need:

- **Python 3.14** (or compatible 3.x version)  
- **MongoDB Community Edition** or access to a MongoDB instance  
- The following Python modules:

