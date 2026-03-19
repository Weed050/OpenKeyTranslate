
let appSettings = {};

window.addEventListener('DOMContentLoaded', () => {
    loadProjects();
    loadSettings();
});

window.openProject = async function(id) {
    console.log("Otwieram projekt o ID:", id);
    
};

async function loadSettings() {
    const response = await fetch("http://127.0.0.1:8000/settings");
    appSettings = await response.json();
    document.getElementById('currentWorkspacePath').innerText = appSettings.app_root_dir;
}

document.getElementById('changeWorkspaceBtn').addEventListener('click', async () => {
    const response = await fetch("http://127.0.0.1:8000/select-folder");
    const data = await response.json();

    if(data.path) {
        // wybor urzytkownika
        const shouldMigrate = confirm(
            "Czy chcesz PRZENIEŚĆ obecne projekty i bazę danych do nowej lokalizacji?\n\n" +
            "OK - Przenieś pliki\n" +
            "Anuluj - Tylko zmień folder (stworzy nową, pustą instancję)"
        );

        const payload = {
            ...appSettings,
            app_root_dir: data.path,
            migrate_data: shouldMigrate
        };

        const saveRes = await fetch("http://127.0.0.1:8000/settings", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
        });

        const result = await saveRes.json();
            alert(result.message); 
            document.body.innerHTML = `
                <div style="text-align:center; margin-top:100px; font-family:sans-serif;">
                    <h1>Wymagany restart</h1>
                    <p>${result.message}</p>
                    <p>Zamknij okno terminala z backendem i uruchom go ponownie.</p>
                </div>`
        location.reload();
    }
});

async function loadProjects() {
    const response = await fetch("http://127.0.0.1:8000/projects");
    const projects = await response.json();
    const listDiv = document.getElementById('projectList');

if (projects.length === 0) {
        listDiv.innerHTML = "<p>Brak aktywnych projektów. Stwórz pierwszy poniżej!</p>";
    } else {
        listDiv.innerHTML = projects.map(p => 
            `<div class="project-tile">
                <strong>${p.name}</strong> - <code>${p.workspace_path}</code>
                <button onclick="openProject(${p.id})">Otwórz</button>
            </div>`
        ).join('');
    }
}

document.getElementById('selectFolderBtn').addEventListener('click', async () => {
    const response = await fetch("http://127.0.0.1:8000/select-folder");
    const data = await response.json();

    if(data.path) {
        document.getElementById('pathPreview').innerText = data.path;
        const projectInput = document.getElementById('projectName');

       if (projectInput.value.trim() === "") {

            const pathParts = data.path.split(/[\\/]/).filter(part => part.length > 0);
            const autoFolderName = pathParts.pop();
            if (autoFolderName) {
                projectInput.value = autoFolderName;
            }
       }
    }
});

document.getElementById('importBtn').addEventListener('click', async () => {
    const path = document.getElementById('pathPreview').innerText;
    const projectName = document.getElementById('projectName').value;

    if(path === "Nie wybrano") return alert("Wybierz folder!");

    const response = await fetch("http://127.0.0.1:8000/import-folder", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ path: path, project_name: projectName })
    });
    
    const result = await response.json();
    alert(result.message);
    loadProjects(); 
});