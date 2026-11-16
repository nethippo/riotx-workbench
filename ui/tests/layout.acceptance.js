// Browser regression: pass this file to Playwright browser_run_code_unsafe on
// an isolated Workbench page with demo jobs. No forms are submitted.
async (page) => {
  const results=[];
  const assert=(condition,message)=>{if(!condition)throw new Error(message)};
  const sizes=[[1600,1000],[1280,720],[1024,600],[800,600],[640,480],[390,360],[320,480]];
  for(const [width,height] of sizes){
    await page.setViewportSize({width,height});
    await page.evaluate(()=>scrollTo(0,0));
    if(!await page.locator('.drawer-panel').evaluate(e=>e.classList.contains('open')))
      await page.locator('.drawer-rail button').nth(1).click();
    await page.waitForTimeout(260); // Drawer transition must finish before geometry assertions.
    const layout=await page.evaluate(()=>({
      viewport:[innerWidth,innerHeight],pageWidth:document.documentElement.scrollWidth,
      panel:document.querySelector('.drawer-panel').getBoundingClientRect().toJSON(),
      contentHeight:document.querySelector('.drawer-content').clientHeight,
      rail:document.querySelector('.drawer-rail').getBoundingClientRect().toJSON()
    }));
    assert(layout.pageWidth<=width+1,`Page overflows horizontally: ${width}x${height}`);
    assert(layout.panel.x>=0&&layout.panel.right<=width&&layout.panel.bottom<=height&&layout.panel.top>=0,'Drawer outside viewport');
    assert(layout.contentHeight>=100,'Drawer body inaccessible');
    assert(layout.rail.bottom<=height-(width<=620?46:0),'Rail overlaps execution history');
    // Verify the bottom of a long page can be reached through normal scrolling.
    const end=await page.evaluate(()=>{window.scrollTo(0,document.documentElement.scrollHeight);return {top:scrollY,bottom:innerHeight+scrollY,height:document.documentElement.scrollHeight}});
    assert(end.bottom>=end.height-2,'Page bottom cannot be reached');
    await page.evaluate(()=>scrollTo(0,0));
    const dialogs=[];
    for(const opener of ['.global-settings-trigger','.top-actions button:has-text("연결 관리")','.side-title button','.trash-link']){
      await page.locator(opener).click();
      const dialog=page.getByRole('dialog');
      const geometry=await dialog.evaluate(e=>{const b=e.querySelector('.modal-body');return {title:e.getAttribute('aria-label'),rect:e.getBoundingClientRect().toJSON(),bodyWidth:b.clientWidth,scrollWidth:b.scrollWidth,bodyHeight:b.clientHeight,bodyScroll:b.scrollHeight,locked:document.body.style.overflow==='hidden'}});
      assert(Math.abs(geometry.rect.width-width/2)<=1&&Math.abs(geometry.rect.height-height/2)<=1,'Dialog is not 50% of viewport');
      assert(Math.abs(geometry.rect.y-height/4)<=1&&Math.abs(geometry.rect.x-width/4)<=1,'Dialog is not centered');
      assert(geometry.scrollWidth<=geometry.bodyWidth+1,'Dialog body overflows horizontally');
      assert(geometry.locked&&geometry.bodyHeight>0,'Dialog scrolling is not isolated');
      // Scroll to the last action and prove it is inside the body viewport.
      const actions=dialog.locator('.modal-body button:visible');
      if(await actions.count()){
        await actions.last().scrollIntoViewIfNeeded();
        const reachable=await actions.last().evaluate(e=>{const a=e.getBoundingClientRect(),b=e.closest('.modal-body').getBoundingClientRect();return a.bottom<=b.bottom+1&&a.top>=b.top-1});
        assert(reachable,`Last action inaccessible: ${geometry.title}`);
      }
      assert(await dialog.locator('header button').isVisible(),'Close control scrolled out');
      await page.keyboard.press('Escape');
      assert(!await page.getByRole('dialog').count(),'Escape did not close dialog');
      assert(await page.evaluate(()=>document.body.style.overflow!=='hidden'),'Body scrolling not restored');
      dialogs.push(geometry);
    }
    results.push({viewport:[width,height],layout,dialogs});
  }
  return {ok:true,viewportCount:results.length,dialogCount:results.reduce((n,r)=>n+r.dialogs.length,0),results};
}
