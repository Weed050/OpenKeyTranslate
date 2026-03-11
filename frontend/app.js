document.getElementById('uploadBtn').addEventListener('click', async () => {
    const input = document.getElementById('imageInput');
    const file = input.files[0];

    if(!file){
        alert("Wybierz jakiś plik!");
        return;
    }

    const formData = new FormData();
    formData.append("file", file);

    try{
        const response = await fetch("http://127.0.0.1:8000/upload-page", {
            method: "POST",
            body: formData
        });

        const data = await response.json();
        console.log("Odpowiedź serwera: ", data);
        alert(data.message);
    }
    catch(error){
        console.error("Błąd połączenia: ", error);
    }
    
});