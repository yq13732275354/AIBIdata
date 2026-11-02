/* Shared: nav active state + lucide icon loader */
(function(){
  // 1. Mark active nav item from body[data-page]
  const page = document.body.dataset.page;
  if(page){
    document.querySelectorAll('[data-nav]').forEach(el=>{
      el.classList.toggle('active', el.dataset.nav === page);
    });
  }
})();
