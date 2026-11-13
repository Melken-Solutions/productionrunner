(() => {
 const form=document.querySelector('[data-bulk-import]');if(!form)return;
 const files=form.elements.files,folder=form.elements.folder_files,status=form.querySelector('[data-import-selection]');
 const supported=f=>/\.(pdf|dxf)$/i.test(f.name);
 function update(){
   const chosen=[...files.files,...folder.files].filter(supported);
   const bytes=chosen.reduce((n,f)=>n+f.size,0);
   status.textContent=`${chosen.length} PDF/DXF files selected · ${(bytes/1024/1024).toFixed(1)} MB. Folder subfolders are included. Review the preview before importing.`;
   files.setCustomValidity('');
   if(!chosen.length)files.setCustomValidity('Choose PDF/DXF files or a folder containing them.');
   else if(chosen.length>400||bytes>50*1024*1024)files.setCustomValidity('Split this selection into batches of at most 400 files and 50 MB.');
   else if(chosen.some(f=>f.size>(/\.pdf$/i.test(f.name)?10:20)*1024*1024))files.setCustomValidity('A file exceeds its limit: PDF 10 MB; DXF 20 MB.');
 }
 files.addEventListener('change',update);folder.addEventListener('change',update);
 form.addEventListener('formdata',event=>{
   // Explicit filenames preserve relative paths consistently across directory pickers.
   event.formData.delete('folder_files');
   for(const file of folder.files)if(supported(file))event.formData.append('folder_files',file,file.webkitRelativePath||file.name);
 });
 form.addEventListener('submit',event=>{update();if(!form.reportValidity()){event.preventDefault();return;}status.textContent='Uploading for review — wait for the preview. Nothing is imported until you confirm.';});
})();
