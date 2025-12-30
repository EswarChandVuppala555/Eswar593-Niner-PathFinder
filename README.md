## Run the App

1. **Install software**

    - [**Visual Studio Code**] integrated development enviornment - optional 

    - [**Python Language**] 3.13 is used within the project but any recent version (3.11 or later) will probably work

    - [**Git**](https://git-scm.com/downloads) version control software

    - [**Docker Desktop**](https://git-scm.com/downloads) containerization software

2. **Clone repository using Git** - From the command line, run:

    ```bash
    git clone https://github.com/EswarChandVuppala555/Eswar593-Niner-PathFinder
    ```

3. **Set up environmental variables** - In the project folder, create a file called **.env** according to .env.example with necessary secrets.

4. **Run with Docker**

    - Start Docker Desktop.  Then, from the command line in the project folder, run:

    ```bash
    docker compose -f docker-compose.yml --env-file .env up -d --build
    ```
5. **Restaring Backend container**
   - After making changes to some backend code, use,
    ```bash
    docker compose restart chat-backend
    # or, more safely:
    docker compose down
    docker compose up --build
    ```

6. **Chat!** - To interact with the Streamlit: Open a web browser and navigate to [**localhost:8501**](localhost:8501)

7. **API Error: Internal Server Error** - If you get this error in the frontend for every question in the LLM chat, then in the project folder in cmd,
    - 1 Type **docker ps**
    - 2 Get the container ID(######) for the chat-backend image
    - 3 Now type **docker logs ######**
    - 4 You can check the error that causing the LLM with API error.
   
8. **Shutdown/cleanup** - Remember to shut down and clean up by deleting your Docker Containers and Docker Images in Docker Desktop - otherwise it'll keep running in the background


### **Imp, to save the changes of local repo (files) into GitHub Repository**
- 1st step - Go to Git Bash and into the project folder (niner-pathfinder)
- If you have spaces in the file name then put double quotes for the complete file.
1. git pull origin main   ------- This makes sure your local copy has the latest changes from GitHub before you add your own edits. (If you’re working on multiple machines or with teammates)
If you forget this command before start and get an error message due to not saving the GitHub changes into local repo (files) Then do this,
1. git pull origin main --rebase   ------- This updates your local repo (local files) by replaying your changes on top of what’s already on GitHub.
 
2. git status   ------- To check the modifications files names from the project folder

3. git add .  (or) git add filename.py   ------- (Prepares locally) This adds all modified files into GitHub
3. git status   ------ Check again for all modifications/ updated
4. git commit -m "Enhanced feature X in chat_backend"   ------- (Saves locally) Commit the changes
5. git push   ------ (This is the main command) (Updates in GitHub) For saving and sending changes to the GitHub repository


So from now on, the cycle is:
git pull → edit → git add → git commit → git push → Good 

## Project Structure

### Folders
```
├─ **chat_backend** - Files for the backend  
├─ **chat_frontend_streamlit** - Files for working frontend using Streamlit
├─ **chroma_loader** - Loads the Chroma vector database on startup, then stops
├─ **docs** - Contains additional documentation including diagrams
├─ **evaluation** - Contains evaluation results
├─ **rag_corpus** - Contains all files (including metadata) comprising the retrieval-augmented generation (RAG) corpus, including originals documents, partially-processed copies, and fully-processed documents.
 ├─ **/original** - contains original, unaltered files
 ├─ **/preprocessing** - contains partially-processed files, including results of scraping
 ├─ **/staged** - contains fully-processed documents staged for production.  NOTE: In production, the Dockerfiles copy this folder or certain contents to their respective /app/rag_corpus folders, omitting "staged".  The entire folder is provided to the backend and to the Chroma loader, while the frontend takes only enough information to populate dropdown menus.
```
### Docker-Compose Containers 
For going into Docker container in the power shell use this code line in the power shell,
    docker compose exec chat-backend sh

The Docker deployment consists of four containers:

- **chat_backend** - FastAPI interface that handles chat requests, coordinating planning, information retrieval, and generation tasks and providing responses to the frontend (or evaluation scripts)  

- **chat_frontend_streamlit** - interactive application that sends chat requests to the backend and displays responses 

- **chroma** - vector database that supports semantic search for retrieval-augmented generation

- **chroma_loader** - loads the RAG corpus into the Chroma vector database

## Development Target

### System Diagram

<img width="1000" alt="A system diagram covering the prompt and response processes" src="docs\system_diagram.png">

### Chat App Interface

<div align="center">
<img width="1000" alt="interface layout" src="docs\interface_layout.svg">
</div>

